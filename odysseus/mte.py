"""
Conector do Mediador/MTE.

O site do Mediador usa HTML antigo e pode exigir reCAPTCHA/sessão.
Este conector faz a parte limpa do processo:

1. Abre sessão no Mediador.
2. Consulta o endpoint identificado no HAR.
3. Trata "Nenhum registro encontrado" como resultado normal.
4. Parseia HTML positivo e extrai:
   - número do registro
   - número da solicitação
   - vigência
   - data de registro, quando aparecer
   - link de download/visualização, quando aparecer

Ele NÃO burla captcha. Se o MTE exigir reCAPTCHA, o retorno vem como status
"captcha_or_blocked" para a gente tratar no próximo passo.
"""

import os
import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

try:
    import requests
except ModuleNotFoundError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:
    BeautifulSoup = None


INSTRUMENT_TYPES = {
    "acordo": "Acordo Coletivo",
    "convencao": "Convenção Coletiva",
    "termoAditivoAcordo": "Termo Aditivo de Acordo Coletivo",
    # Nome mantido como aparece no site/HAR, com o typo do próprio endpoint.
    "termoAditivoConvecao": "Termo Aditivo de Convenção Coletiva",
}

SITE_ROOT = "https://www3.mte.gov.br"
BASE_URL = SITE_ROOT + "/sistemas/mediador/ConsultarInstColetivo"
SEARCH_URL = BASE_URL + "/getConsultaAvancada"
TOKEN_URL = BASE_URL + "/GenerateSecurityToken"
DOWNLOAD_URL = SITE_ROOT + "/sistemas/mediador/Resumo/resumoVisualizarSalvarMsWordDoc?NrSolicitacao="


def getsec(cfg, name, default=None):
    try:
        return cfg.get("mte", {}).get(name, default)
    except AttributeError:
        return default


def cleanstr(val):
    return re.sub(r"\s+", " ", str(val or "")).strip()


def cleancnpj(val):
    return re.sub(r"\D+", "", str(val or ""))


def todate(val):
    val = cleanstr(val)
    if not val:
        return ""

    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", val)
    if not m:
        return val

    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def gettext(raw):
    raw = raw or ""

    if BeautifulSoup:
        soup = BeautifulSoup(raw, "html.parser")
        return cleanstr(soup.get_text(" ", strip=True))

    raw = re.sub(r"<script.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return cleanstr(unescape(raw))


def pick(text, pats):
    for pat in pats:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            return cleanstr(m.group(1))
    return ""


def pickdates(text):
    pats = [
        r"Vig[êe]ncia\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})\s*(?:a|até|ate|à|-)\s*(\d{2}/\d{2}/\d{4})",
        r"Per[íi]odo\s+de\s+Vig[êe]ncia\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})\s*(?:a|até|ate|à|-)\s*(\d{2}/\d{2}/\d{4})",
    ]

    for pat in pats:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            return todate(m.group(1)), todate(m.group(2))

    return "", ""


def hrefs(raw):
    out = []

    for h in re.findall(r'href=["\']([^"\']+)["\']', raw or "", flags=re.I):
        if not h:
            continue

        url = urljoin(SITE_ROOT, unescape(h))
        out.append(url)

    return out


def findurl(raw, req):
    if not req:
        return ""

    reqplain = unquote(req)

    for url in hrefs(raw):
        chk = unquote(url)

        if reqplain in chk:
            return url

        if quote(reqplain, safe="") in chk:
            return url

    return DOWNLOAD_URL + quote(reqplain, safe="")


def nonefound(raw):
    text = gettext(raw).lower()

    checks = [
        "nenhum registro encontrado",
        "nenhum instrumento encontrado",
        "não foram encontrados registros",
        "nao foram encontrados registros",
    ]

    return any(item in text for item in checks)


def blocked(raw, code):
    text = (raw or "").lower()

    if code in (401, 403, 429):
        return True

    checks = [
        "recaptcha",
        "g-recaptcha",
        "captcha",
        "cloudflare",
        "access denied",
        "acesso negado",
        "forbidden",
    ]

    return any(item in text for item in checks)


class MteClient:
    def __init__(self, cfg):
        if requests is None:
            raise RuntimeError(
                "Instale a dependência requests: pip install requests"
            )

        self.cfg = cfg
        self.timeout = int(getsec(cfg, "timeout", 60))
        self.delay = float(getsec(cfg, "delay", 0.8))
        self.maxpages = int(getsec(cfg, "max_pages", 10))
        self.token = (
            os.getenv("ODYSSEUS_RECAPTCHA_TOKEN")
            or getsec(cfg, "recaptcha_token", "")
            or ""
        )

        self.ses = requests.Session()
        self.ses.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": SITE_ROOT,
            "Referer": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
        })

    def build_payload(self, cnpj, uf, instrument_type, page=1, total=0, recaptcha_token=""):
        cnpj = cleancnpj(cnpj)
        uf = cleanstr(uf).upper()
        recaptcha_token = recaptcha_token or self.token

        return {
            "nrCnpj": cnpj,
            "tpRequerimento": instrument_type,
            "tpVigencia": "VIGENTE",
            "sgUfDeRegistro": uf,
            "ufsAbrangidasTotalmente": uf,
            "pagina": str(page),
            "qtdTotalRegistro": str(total or 0),
            "recaptchaToken": recaptcha_token,
        }

    def start(self):
        res = self.ses.get(BASE_URL, timeout=self.timeout)
        return res

    def pages(self, raw):
        text = gettext(raw)

        total = 0
        pagecount = 1

        m = re.search(r"(\d+)\s+Instrumento\(s\)", text, flags=re.I)
        if m:
            total = int(m.group(1))

        m = re.search(r"P[áa]gina\s+\d+\s+de\s+(\d+)", text, flags=re.I)
        if m:
            pagecount = int(m.group(1))

        return total, max(pagecount, 1)

    def parse(self, raw, cnpj, uf, instrument_type):
        text = gettext(raw)
        total, pagecount = self.pages(raw)

        marks = list(re.finditer(r"N[ºo]\s+do\s+Registro", text, flags=re.I))
        items = []

        if not marks:
            return {
                "total_count": total,
                "page_count": pagecount,
                "items": [],
                "text": text,
            }

        for i, mark in enumerate(marks):
            start = mark.start()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            block = text[start:end]

            reg = pick(block, [
                r"N[ºo]\s+do\s+Registro\s*[:\-]?\s*([A-Z0-9./\-]+)",
                r"Registro\s*[:\-]?\s*([A-Z]{2}\d{6}/\d{4})",
            ])

            req = pick(block, [
                r"N[ºo]\s+da\s+Solicita[çc][ãa]o\s*[:\-]?\s*([A-Z0-9./\-]+)",
                r"Solicita[çc][ãa]o\s*[:\-]?\s*([A-Z]{2}\d{6}/\d{4})",
            ])

            date = pick(block, [
                r"Data\s+d[eo]\s+Registro\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
                r"Registrado\s+em\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
                r"Registro\s+em\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
            ])

            vigini, vigfim = pickdates(block)

            item = {
                "sindicato_cnpj": cleancnpj(cnpj),
                "tipo_instrumento": instrument_type,
                "tipo_descricao": INSTRUMENT_TYPES.get(instrument_type, instrument_type),
                "numero_registro": reg,
                "numero_solicitacao": req,
                "data_registro": todate(date),
                "vigencia_inicio": vigini,
                "vigencia_fim": vigfim,
                "uf": cleanstr(uf).upper(),
                "url_documento": findurl(raw, req),
                "raw_text": block[:4000],
            }

            if item["numero_registro"] or item["numero_solicitacao"]:
                items.append(item)

        return {
            "total_count": total,
            "page_count": pagecount,
            "items": items,
            "text": text,
        }

    def search(self, cnpj, uf, instrument_type):
        cnpj = cleancnpj(cnpj)
        uf = cleanstr(uf).upper()

        if instrument_type not in INSTRUMENT_TYPES:
            raise ValueError(f"Tipo de instrumento inválido: {instrument_type}")

        self.start()

        allitems = []
        seen = set()
        total = 0
        pages = 1
        lastcode = None
        lasttext = ""
        pagesdone = 0

        for page in range(1, self.maxpages + 1):
            data = self.build_payload(
                cnpj=cnpj,
                uf=uf,
                instrument_type=instrument_type,
                page=page,
                total=total,
            )

            res = self.ses.post(
                SEARCH_URL,
                data=data,
                timeout=self.timeout,
            )

            lastcode = res.status_code
            raw = res.text or ""
            lasttext = gettext(raw)

            if nonefound(raw):
                if page == 1:
                    return {
                        "ok": True,
                        "status": "none",
                        "message": "Nenhum registro encontrado.",
                        "cnpj": cnpj,
                        "uf": uf,
                        "type": instrument_type,
                        "type_label": INSTRUMENT_TYPES[instrument_type],
                        "total": 0,
                        "items": [],
                        "http_code": lastcode,
                    }

                print(f"    página {page}: nenhum registro novo. Parando.")
                break

            if blocked(raw, lastcode):
                return {
                    "ok": False,
                    "status": "captcha_or_blocked",
                    "message": "O MTE exigiu captcha, bloqueou a sessão ou recusou a consulta.",
                    "cnpj": cnpj,
                    "uf": uf,
                    "type": instrument_type,
                    "type_label": INSTRUMENT_TYPES[instrument_type],
                    "total": len(allitems),
                    "items": allitems,
                    "http_code": lastcode,
                    "raw_text": lasttext[:1500],
                }

            if lastcode >= 400:
                return {
                    "ok": False,
                    "status": "http_error",
                    "message": f"Erro HTTP {lastcode} ao consultar o MTE.",
                    "cnpj": cnpj,
                    "uf": uf,
                    "type": instrument_type,
                    "type_label": INSTRUMENT_TYPES[instrument_type],
                    "total": len(allitems),
                    "items": allitems,
                    "http_code": lastcode,
                    "raw_text": lasttext[:1500],
                }

            parsed = self.parse(raw, cnpj, uf, instrument_type)

            parsedtotal = parsed.get("total_count") or 0
            parsedpages = parsed.get("page_count") or 0

            if parsedtotal > 0:
                total = parsedtotal

            if parsedpages > 1:
                pages = max(pages, parsedpages)

            items = parsed.get("items", [])
            added = 0

            for item in items:
                key = (
                    item.get("numero_registro") or "",
                    item.get("numero_solicitacao") or "",
                    item.get("tipo_instrumento") or "",
                )

                if key in seen:
                    continue

                seen.add(key)
                allitems.append(item)
                added += 1

            pagesdone = page

            print(f"    página {page}: extraídos {len(items)} | novos {added}")

            if len(items) == 0:
                break

            if page > 1 and added == 0:
                print("    próxima página retornou itens repetidos. Parando.")
                break

            if len(items) < 10:
                break

            time.sleep(self.delay)

        return {
            "ok": True,
            "status": "ok" if allitems else "empty_unparsed",
            "message": "Consulta concluída.",
            "cnpj": cnpj,
            "uf": uf,
            "type": instrument_type,
            "type_label": INSTRUMENT_TYPES[instrument_type],
            "total": len(allitems),
            "expected_total": total,
            "pages": pagesdone,
            "items": allitems,
            "http_code": lastcode,
            "raw_text": lasttext[:1500],
        }    

    def download(self, url, path):
        if not url:
            raise ValueError("URL do documento não informada.")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        res = self.ses.get(url, timeout=self.timeout)

        if res.status_code >= 400:
            raise RuntimeError(f"Erro HTTP {res.status_code} ao baixar documento do MTE.")

        path.write_bytes(res.content)

        return str(path)