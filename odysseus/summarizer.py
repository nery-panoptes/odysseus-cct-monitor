import difflib
import html
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}

SKIP_TAGS = {"script", "style", "noscript"}

TOPIC_KEYWORDS = [
    (
        "vigencia",
        [
            "vigencia",
            "data base",
            "data-base",
            "abrangencia",
            "periodo",
        ],
    ),
    (
        "salarios e reajustes",
        [
            "reajuste",
            "salario",
            "piso",
            "remuneracao",
            "gratificacao",
            "adicional",
            "percentual",
            "r$",
            "por cento",
        ],
    ),
    (
        "beneficios",
        [
            "auxilio",
            "vale",
            "alimentacao",
            "refeicao",
            "cesta",
            "plano de saude",
            "assistencia medica",
            "seguro",
            "premio",
        ],
    ),
    (
        "jornada",
        [
            "jornada",
            "banco de horas",
            "compensacao",
            "horas extras",
            "escala",
            "intervalo",
            "sobreaviso",
            "teletrabalho",
            "home office",
        ],
    ),
    (
        "contribuicoes e descontos",
        [
            "contribuicao",
            "desconto",
            "mensalidade",
            "sindical",
            "assistencial",
            "confederativa",
            "oposicao",
            "taxa",
        ],
    ),
    (
        "prazos e obrigacoes",
        [
            "prazo",
            "multa",
            "penalidade",
            "obrigatorio",
            "devera",
            "comunicacao",
            "comprovante",
            "relacao",
            "email",
        ],
    ),
    (
        "saude e seguranca",
        [
            "seguranca",
            "saude",
            "epi",
            "insalubridade",
            "periculosidade",
            "atestado",
            "medico",
        ],
    ),
]

NUMERIC_RE = re.compile(
    r"R\$\s*\d[\d.\s]*(?:,\d{2})?|\d+(?:,\d+)?\s*%|\d+\s*\([^)]+\)|\b\d+\s*(?:dias?|m[eê]s(?:es)?|horas?|anos?)\b",
    re.I,
)


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = (tag or "").lower()

        if tag in SKIP_TAGS:
            self.skip_depth += 1

        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = (tag or "").lower()

        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth and data:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def _sec(cfg, name):
    try:
        return cfg.get(name, {})
    except AttributeError:
        return {}


def _summary_cfg(cfg):
    return _sec(cfg, "summary")


def summary_enabled(cfg):
    return bool(_summary_cfg(cfg).get("enabled", True))


def strip_accents(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def norm(value):
    text = strip_accents(value).lower()
    text = re.sub(r"[^a-z0-9%$.,/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(value):
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact(value, limit=300):
    text = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(text) <= limit:
        return text

    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{cut}..."


def decode_bytes(data):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def detect_file_type(path, content_type=""):
    path = Path(path)
    suffix = path.suffix.lower()
    content_type = str(content_type or "").split(";")[0].strip().lower()

    with path.open("rb") as src:
        head = src.read(8192)

    sample = head.lstrip().lower()

    if head.startswith(b"%PDF-") or content_type == "application/pdf":
        return {
            "kind": "pdf",
            "extension": "pdf",
            "label": "PDF",
            "mime": content_type or "application/pdf",
        }

    if head.startswith(b"{\\rtf") or suffix == ".rtf":
        return {
            "kind": "rtf",
            "extension": "rtf",
            "label": "RTF",
            "mime": content_type or "application/rtf",
        }

    if head.startswith(b"PK\x03\x04") or suffix == ".docx":
        return {
            "kind": "docx",
            "extension": "docx",
            "label": "DOCX",
            "mime": content_type
            or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return {
            "kind": "doc_binary",
            "extension": "doc",
            "label": "Word DOC binário",
            "mime": content_type or "application/msword",
        }

    html_like = (
        b"<html" in sample[:2000]
        or b"<!doctype html" in sample[:2000]
        or b"<body" in sample[:2000]
    )

    if html_like:
        label = "HTML salvo como DOC" if suffix == ".doc" else "HTML"
        return {
            "kind": "html",
            "extension": "doc" if suffix == ".doc" else "html",
            "label": label,
            "mime": content_type or "text/html",
        }

    return {
        "kind": "text",
        "extension": suffix.lstrip(".") or "txt",
        "label": "Texto",
        "mime": content_type or "text/plain",
    }


def html_to_text(raw):
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        return clean_text(soup.get_text("\n", strip=True))
    except Exception:
        parser = _PlainTextHTMLParser()
        parser.feed(raw)
        return clean_text(parser.text())


def rtf_to_text(raw):
    text = raw
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = text.replace("\\", " ")
    return clean_text(text)


def docx_to_text(path):
    out = []

    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as src:
            xml = src.read().decode("utf-8", errors="replace")

    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", " ", xml)
    out.append(html.unescape(xml))
    return clean_text("\n".join(out))


def textutil_to_text(path):
    if not shutil.which("textutil"):
        return ""

    try:
        res = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except Exception:
        return ""

    if res.returncode != 0:
        return ""

    return clean_text(decode_bytes(res.stdout))


def pdf_to_text(path, cfg=None):
    warnings = []
    text = ""

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []

        for page in reader.pages:
            pages.append(page.extract_text() or "")

        text = clean_text("\n".join(pages))
    except Exception as err:
        warnings.append(f"pypdf indisponível ou falhou: {err}")

    if not text and shutil.which("pdftotext"):
        try:
            res = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                check=False,
                capture_output=True,
                timeout=60,
            )

            if res.returncode == 0:
                text = clean_text(decode_bytes(res.stdout))
        except Exception as err:
            warnings.append(f"pdftotext falhou: {err}")

    if text:
        return text, False, False, warnings

    scfg = _summary_cfg(cfg)
    ocr_enabled = bool(scfg.get("ocr_enabled", False))

    if not ocr_enabled:
        warnings.append(
            "PDF sem texto extraível; OCR gratuito pode ser ativado com ocrmypdf/tesseract."
        )
        return "", True, False, warnings

    if not shutil.which("ocrmypdf"):
        warnings.append("OCR solicitado, mas o comando ocrmypdf não foi encontrado.")
        return "", True, False, warnings

    lang = str(scfg.get("ocr_language", "por") or "por")

    with tempfile.TemporaryDirectory() as tmpdir:
        sidecar = Path(tmpdir) / "ocr.txt"
        outpdf = Path(tmpdir) / "ocr.pdf"

        try:
            res = subprocess.run(
                [
                    "ocrmypdf",
                    "-l",
                    lang,
                    "--sidecar",
                    str(sidecar),
                    str(path),
                    str(outpdf),
                ],
                check=False,
                capture_output=True,
                timeout=300,
            )
        except Exception as err:
            warnings.append(f"OCR falhou: {err}")
            return "", True, False, warnings

        if res.returncode != 0:
            msg = decode_bytes(res.stderr).strip()
            warnings.append(f"OCR falhou: {compact(msg, 220)}")
            return "", True, False, warnings

        text = clean_text(sidecar.read_text(encoding="utf-8", errors="replace"))

    return text, not bool(text), bool(text), warnings


def extract_text_from_file(path, cfg=None):
    path = Path(path)

    if not path.exists():
        return {
            "ok": False,
            "text": "",
            "file_type": "arquivo ausente",
            "kind": "missing",
            "mime": "",
            "extension": path.suffix.lstrip("."),
            "ocr_needed": False,
            "ocr_used": False,
            "warnings": [f"Arquivo não encontrado: {path}"],
        }

    info = detect_file_type(path)
    warnings = []
    text = ""
    ocr_needed = False
    ocr_used = False

    try:
        if info["kind"] == "html":
            text = html_to_text(decode_bytes(path.read_bytes()))
        elif info["kind"] == "rtf":
            text = textutil_to_text(path) or rtf_to_text(decode_bytes(path.read_bytes()))
        elif info["kind"] == "docx":
            text = docx_to_text(path)
        elif info["kind"] == "doc_binary":
            text = textutil_to_text(path)
            if not text:
                warnings.append(
                    "DOC binário sem texto extraído; use conversão local ou OCR se for imagem."
                )
        elif info["kind"] == "pdf":
            text, ocr_needed, ocr_used, warnings = pdf_to_text(path, cfg=cfg)
        else:
            text = clean_text(decode_bytes(path.read_bytes()))
    except Exception as err:
        warnings.append(str(err))

    return {
        "ok": bool(text),
        "text": text,
        "file_type": info["label"],
        "kind": info["kind"],
        "mime": info["mime"],
        "extension": info["extension"],
        "ocr_needed": bool(ocr_needed),
        "ocr_used": bool(ocr_used),
        "warnings": warnings,
    }


def split_clauses(text):
    lines = [line.strip() for line in clean_text(text).splitlines() if line.strip()]
    clauses = []
    current_title = ""
    current_body = []

    title_re = re.compile(r"^CL[ÁA]USULA\s+.{1,180}$", re.I)

    for line in lines:
        if title_re.match(line):
            if current_title or current_body:
                clauses.append((current_title, " ".join(current_body).strip()))

            current_title = line
            current_body = []
            continue

        current_body.append(line)

    if current_title or current_body:
        clauses.append((current_title, " ".join(current_body).strip()))

    if len(clauses) <= 1:
        chunks = re.split(r"(?<=[.;:])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", clean_text(text))
        clauses = [("", chunk.strip()) for chunk in chunks if len(chunk.strip()) > 40]

    return clauses


def topic_hits(text):
    normalized = norm(text)
    hits = []

    for topic, keywords in TOPIC_KEYWORDS:
        if any(norm(keyword) in normalized for keyword in keywords):
            hits.append(topic)

    return hits


def score_clause(title, body):
    full = f"{title} {body}"
    normalized = norm(full)
    score = 0

    for _, keywords in TOPIC_KEYWORDS:
        score += sum(1 for keyword in keywords if norm(keyword) in normalized)

    score += min(len(NUMERIC_RE.findall(full)), 4)

    if title:
        score += 2

    return score


def relevant_snippets(text, limit=8):
    snippets = []

    for title, body in split_clauses(text):
        score = score_clause(title, body)

        if score <= 1:
            continue

        topics = topic_hits(f"{title} {body}")
        prefix = title.strip()
        body_first = compact(body, 260)

        if prefix and body_first:
            snippet = f"{prefix}: {body_first}"
        else:
            snippet = body_first or compact(prefix, 260)

        snippets.append({
            "score": score,
            "topics": topics,
            "text": snippet,
        })

    snippets.sort(key=lambda item: item["score"], reverse=True)

    selected = []
    seen_topics = set()

    for item in snippets:
        topics = tuple(item["topics"])

        if topics and topics in seen_topics and len(selected) >= 3:
            continue

        selected.append(item["text"])
        seen_topics.add(topics)

        if len(selected) >= limit:
            break

    return selected


def find_vigencia(text):
    pats = [
        r"vig[êe]ncia[^.\n]{0,180}?(\d{1,2}[ºo]?\s+de\s+[a-zç]+(?:\s+de)?\s+\d{4})\s+a\s+(\d{1,2}\s+de\s+[a-zç]+(?:\s+de)?\s+\d{4})",
        r"vig[êe]ncia[^.\n]{0,180}?(\d{2}/\d{2}/\d{4})\s*(?:a|ate|até|-)\s*(\d{2}/\d{2}/\d{4})",
    ]

    for pat in pats:
        match = re.search(pat, text, flags=re.I)
        if match:
            return compact(f"{match.group(1)} a {match.group(2)}", 160)

    return ""


def fingerprint(value):
    text = norm(value)
    text = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " DATA ", text)
    text = re.sub(r"\b\d{4}\b", " ANO ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_similar_to_any(text, candidates, threshold=0.86):
    fp = fingerprint(text)

    for candidate in candidates:
        if difflib.SequenceMatcher(None, fp, candidate).ratio() >= threshold:
            return True

    return False


def compare_with_previous(new_text, previous_text, limit=4):
    new_snippets = relevant_snippets(new_text, limit=12)
    old_fingerprints = [fingerprint(item) for item in relevant_snippets(previous_text, limit=30)]
    changes = []

    for snippet in new_snippets:
        if not is_similar_to_any(snippet, old_fingerprints):
            changes.append(
                "Aparece no documento novo e não foi localizado de forma equivalente no anterior: "
                + compact(snippet, 260)
            )

        if len(changes) >= limit:
            break

    return changes


def key_points(text, limit=4):
    points = []
    vigencia = find_vigencia(text)

    if vigencia:
        points.append(f"Vigência/data-base: {vigencia}.")

    for snippet in relevant_snippets(text, limit=limit + 2):
        short = compact(snippet, 260)

        if short and short not in points:
            points.append(short)

        if len(points) >= limit:
            break

    return points


def build_document_summary(path, item=None, previous_path=None, cfg=None):
    scfg = _summary_cfg(cfg)
    max_bullets = int(scfg.get("max_bullets", 5) or 5)
    include_file_type = bool(scfg.get("include_file_type_note", True))

    current = extract_text_from_file(path, cfg=cfg)
    lines = []

    if include_file_type:
        if current["kind"] == "html":
            lines.append("Arquivo textual .doc/HTML do MTE; OCR não necessário.")
        elif current["kind"] == "pdf" and current["ocr_needed"]:
            lines.append("PDF sem texto extraível; OCR gratuito será necessário para resumir.")
        elif current["kind"] == "pdf":
            lines.append("PDF com texto extraível; OCR não necessário.")
        else:
            lines.append(f"Tipo detectado: {current['file_type']}.")

    if not current["ok"]:
        reason = "; ".join(current["warnings"][:2]) or "texto não extraído"
        lines.append(f"Resumo automático não gerado: {compact(reason, 220)}")
        return {
            "status": "extract_failed",
            "summary": "\n".join(lines),
            "file_type": current["file_type"],
            "ocr_needed": current["ocr_needed"],
            "ocr_used": current["ocr_used"],
            "warnings": current["warnings"],
        }

    previous_text = ""

    if previous_path and Path(previous_path).exists():
        previous = extract_text_from_file(previous_path, cfg=cfg)

        if previous["ok"]:
            previous_text = previous["text"]

    if previous_text:
        changes = compare_with_previous(
            current["text"],
            previous_text,
            limit=max(1, max_bullets - len(lines)),
        )

        if changes:
            lines.extend(changes)
        else:
            lines.append(
                "Comparação local não encontrou diferenças relevantes nos pontos monitorados; confira o anexo para validação."
            )
    else:
        lines.append(
            "Sem arquivo anterior comparável com texto salvo; resumo abaixo destaca os pontos principais do documento."
        )
        lines.extend(key_points(current["text"], limit=max(1, max_bullets - len(lines))))

    return {
        "status": "ok",
        "summary": "\n".join(lines[:max_bullets]),
        "file_type": current["file_type"],
        "ocr_needed": current["ocr_needed"],
        "ocr_used": current["ocr_used"],
        "warnings": current["warnings"],
    }
