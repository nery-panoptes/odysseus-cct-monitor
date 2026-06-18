import argparse
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .cfg import loadcfg, sec
from .db import Db
from .emailer import Emailer
from .mte import DOWNLOAD_URL, INSTRUMENT_TYPES, MteClient
from .report import write_csv
from .util import root

def opendb(cfg):
    base = Path(cfg["base"])
    dbpath = sec(cfg, "app").get("db", "data/odysses_cct_base.sqlite")
    return Db(root(base, dbpath))


def cmd_check(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        print("Banco:", db.path)
        print("\nContagens:")

        for key, val in db.counts().items():
            print(f"- {key}: {val}")

    finally:
        db.close()


def cmd_export_candidates(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        rows = db.monitor_candidates(only_with_cnpj=not args.include_no_cnpj)

        out = (
            root(Path(cfg["base"]), sec(cfg, "app").get("reports", "reports"))
            / "monitoramento_candidatos.csv"
        )

        write_csv(out, rows)

        print(f"Candidatos exportados: {len(rows)}")
        print(f"Arquivo: {out}")

    finally:
        db.close()


def cmd_baseline_summary(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        rows = db.known_manual_summary()

        out = (
            root(Path(cfg["base"]), sec(cfg, "app").get("reports", "reports"))
            / "baseline_manual_resumo.csv"
        )

        write_csv(out, rows)

        print(f"Resumo baseline manual exportado: {len(rows)}")
        print(f"Arquivo: {out}")

    finally:
        db.close()


def cmd_email_test(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        if args.create_alert:
            db.create_test_alert()

        alerts = db.pending_alerts()

    finally:
        db.close()

    body = [
        "Bom dia,",
        "",
        "Isso é apenas um teste do sistema de e-mails do Odysséus.",
        "",
        f"Alertas pendentes no banco: {len(alerts)}",
        "",
        "Atenciosamente,",
        "Odysséus, Robô de Monitoramento de Convenções Coletivas",
    ]

    result = Emailer(cfg).send(
        "Teste de envio do Odysséus",
        "\n".join(body),
        attachments=[],
    )

    if result.get("dry_run"):
        print("Dry-run ativo. E-mail gravado em:", result["path"])
    else:
        print("E-mail enviado com sucesso.")

def cmd_seed_baseline(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    limit = getattr(args, "limit", None)

    total_queries = 0
    total_seen = 0
    total_existing = 0
    total_seeded = 0
    errors = []

    try:
        rows = db.monitor_candidates(only_with_cnpj=True)

        if limit:
            rows = rows[:limit]

        client = MteClient(cfg)

        print("Seed baseline iniciado.")
        print(f"Sindicatos candidatos com CNPJ válido: {len(rows)}")
        print("Modo: consultar MTE e marcar tudo como conhecido antes do robô.")
        print("")

        for idx, row in enumerate(rows, start=1):
            name = row.get("nome") or ""
            cnpj = row.get("cnpj") or ""
            ufs = target_ufs(cfg, row)

            print(f"[{idx}/{len(rows)}] {name}")
            print(f"CNPJ: {cnpj} | UFs: {', '.join(ufs)}")

            for uf in ufs:
                print(f"  UF: {uf}")

                for typ in INSTRUMENT_TYPES.keys():
                    total_queries += 1

                    try:
                        result = client.search(
                            cnpj=cnpj,
                            uf=uf,
                            instrument_type=typ,
                        )

                        status = result.get("status")
                        items = result.get("items") or []

                        print(f"    - {typ}: {status} | coletados: {len(items)}")

                        total_seen += len(items)

                        for item in items:
                            item["sindicato_nome"] = name
                            item["tipo_instrumento"] = item.get("tipo_instrumento") or typ
                            item["tipo_descricao"] = INSTRUMENT_TYPES.get(
                                item.get("tipo_instrumento"),
                                item.get("tipo_instrumento") or "",
                            )

                            inst_id, was_new = db.save_mte_instrument(
                                item,
                                sindicato_nome=name,
                                known_before=True,
                            )

                            if was_new:
                                total_seeded += 1
                            else:
                                total_existing += 1

                    except Exception as err:
                        errors.append(f"{name} | {cnpj} | {uf} | {typ}: {err}")
                        print(f"    - {typ}: ERRO | {err}")

            print("")

        print("Seed baseline concluído.")
        print(f"Consultas realizadas: {total_queries}")
        print(f"Instrumentos coletados: {total_seen}")
        print(f"Já existentes no banco: {total_existing}")
        print(f"Novos adicionados como baseline: {total_seeded}")
        print(f"Erros: {len(errors)}")

        if errors:
            print("")
            print("Ocorrências:")

            for err in errors[:20]:
                print(f"- {err}")

            if len(errors) > 20:
                print(f"- ... mais {len(errors) - 20} ocorrência(s).")

    finally:
        db.close()

        print("Seed baseline concluído.")
        print(f"Consultas realizadas: {total_queries}")
        print(f"Instrumentos coletados: {total_seen}")
        print(f"Já existentes no banco: {total_existing}")
        print(f"Novos adicionados como baseline: {total_seeded}")
        print(f"Erros: {len(errors)}")


def cmd_mte_test(args):
    cfg = loadcfg(args.config)

    cnpj = args.cnpj
    uf = args.uf

    if args.first:
        db = opendb(cfg)

        try:
            rows = db.monitor_candidates(only_with_cnpj=True)

            if not rows:
                raise RuntimeError("Nenhum sindicato candidato com CNPJ válido foi encontrado no banco.")

            row = rows[0]
            cnpj = row.get("cnpj") or ""
            uf = row.get("uf_inferida") or uf or "DF"

            print("Usando primeiro candidato do banco:")
            print(f"- {row.get('nome')}")
            print(f"- CNPJ: {cnpj}")
            print(f"- UF: {uf}")
            print("")

        finally:
            db.close()

    if not cnpj:
        raise RuntimeError("Informe --cnpj ou use --first.")

    client = MteClient(cfg)

    print("Consultando Mediador/MTE...")
    print(f"CNPJ: {cnpj}")
    print(f"UF: {uf}")
    print(f"Tipo: {args.type} - {INSTRUMENT_TYPES.get(args.type)}")
    print("")

    result = client.search(
        cnpj=cnpj,
        uf=uf,
        instrument_type=args.type,
    )

    print("Resultado:")
    print(f"- ok: {result.get('ok')}")
    print(f"- status: {result.get('status')}")
    print(f"- http_code: {result.get('http_code')}")
    print(f"- mensagem: {result.get('message')}")
    print(f"- total extraído: {result.get('total', 0)}")
    print(f"- total esperado no HTML: {result.get('expected_total', 0)}")
    print(f"- páginas: {result.get('pages', 1)}")
    print("")

    items = result.get("items") or []

    if items:
        print("Instrumentos encontrados:")

        for item in items:
            print("")
            print(f"- Registro: {item.get('numero_registro')}")
            print(f"  Solicitação: {item.get('numero_solicitacao')}")
            print(f"  Tipo: {item.get('tipo_descricao')}")
            print(f"  Data registro: {item.get('data_registro')}")
            print(f"  Vigência: {item.get('vigencia_inicio')} até {item.get('vigencia_fim')}")
            print(f"  URL: {item.get('url_documento')}")

    else:
        print("Nenhum instrumento extraído nessa consulta.")

    if result.get("status") == "captcha_or_blocked":
        print("")
        print("Atenção:")
        print("O MTE pode ter exigido captcha/sessão. Isso não é falha do banco nem do e-mail.")
        print("Se isso acontecer, o próximo passo será usar sessão local do navegador ou token capturado legitimamente.")

    if args.raw:
        print("")
        print("Trecho bruto retornado:")
        print(result.get("raw_text", ""))


def safe_file_name(value):
    value = str(value or "").strip()
    value = re.sub(r"[^\w\-.]+", "_", value, flags=re.UNICODE)
    value = value.strip("._")
    return value or "instrumento"

def build_daily_body(new_items, errors, finished_at=None, stats=None):
    finished_at = finished_at or datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
    stats = stats or {}

    lines = [
        "Bom dia,",
        "",
        "Odysséus, Robô de Monitoramento de Convenções Coletivas, concluiu a rotina diária de monitoramento no Mediador/MTE.",
        "",
        f"Data e hora de conclusão da busca: {finished_at}.",
        "",
    ]

    if new_items:
        lines.append(f"Foram identificados {len(new_items)} novo(s) instrumento(s) coletivo(s):")
        lines.append("")

        for item in new_items:
            lines.append(f"- Sindicato: {item.get('sindicato_nome') or ''}")
            lines.append(f"  Tipo: {item.get('tipo_descricao') or item.get('tipo_instrumento') or ''}")
            lines.append(f"  Registro: {item.get('numero_registro') or ''}")
            lines.append(f"  Solicitação: {item.get('numero_solicitacao') or ''}")
            lines.append(f"  Vigência: {item.get('vigencia_inicio') or ''} até {item.get('vigencia_fim') or ''}")
            lines.append(f"  UF: {item.get('uf') or ''}")

            if item.get("url_documento"):
                lines.append(f"  Link: {item.get('url_documento')}")

            lines.append("")

        lines.append("Os documentos localizados seguem anexos, quando o download foi concluído com sucesso.")

        if errors:
            lines.append("")
            lines.append(
                "Observação: a rotina foi concluída com ocorrência(s) em algumas consultas. "
                "Os novos instrumentos acima foram identificados nas consultas concluídas com sucesso."
            )

    else:
        lines.append("Nenhum instrumento coletivo novo foi identificado nesta execução.")
        lines.append("")

        if errors:
            lines.append(
                "A rotina foi concluída com ocorrência(s) em algumas consultas. "
                "Não foram identificados novos registros nas consultas concluídas com sucesso."
            )
        else:
            lines.append("A base foi consultada normalmente e não houve novos registros a comunicar.")

    if stats:
        lines.append("")
        lines.append("Resumo da execução:")
        lines.append(f"- Consultas realizadas: {stats.get('total_queries', 0)}")
        lines.append(f"- Instrumentos coletados: {stats.get('total_seen', 0)}")
        lines.append(f"- Instrumentos já existentes no banco: {stats.get('total_existing', 0)}")
        lines.append(f"- Novos instrumentos identificados: {stats.get('total_new', 0)}")
        lines.append(f"- Instrumentos ignorados por ano antigo/sem ano: {stats.get('total_ignored_old', 0)}")
        lines.append(f"- Documentos baixados: {stats.get('total_downloaded', 0)}")
        lines.append(f"- Ocorrências/erros: {len(errors)}")

    if errors:
        lines.append("")
        lines.append("Ocorrências durante a execução:")

        for err in errors[:20]:
            lines.append(f"- {err}")

        if len(errors) > 20:
            lines.append(f"- ... mais {len(errors) - 20} ocorrência(s).")

    lines.extend([
        "",
        "Atenciosamente,",
        "Odysséus, Robô de Monitoramento de Convenções Coletivas",
    ])

    return "\n".join(lines)

def download_daily_doc(client, cfg, item):
    base = Path(cfg["base"])
    downloads = root(base, sec(cfg, "app").get("downloads", "downloads"))
    downloads.mkdir(parents=True, exist_ok=True)

    req = item.get("numero_solicitacao") or ""
    reg = item.get("numero_registro") or ""
    typ = item.get("tipo_instrumento") or "instrumento"

    if not req:
        return ""

    url = DOWNLOAD_URL + quote(req, safe="")
    name = safe_file_name(f"{typ}_{reg}_{req}") + ".doc"
    path = downloads / name

    client.download(url, path)

    return str(path)

def cmd_daily(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    limit = getattr(args, "limit", None)
    no_send = getattr(args, "no_send", False)
    no_download = getattr(args, "no_download", False)

    total_queries = 0
    total_seen = 0
    total_existing = 0
    total_new = 0
    total_downloaded = 0
    total_ignored_old = 0

    errors = []
    new_items = []
    alert_ids = []
    attachments = []
    seen_keys = set()

    try:
        rows = db.monitor_candidates(only_with_cnpj=True)

        if limit:
            rows = rows[:limit]

        client = MteClient(cfg)

        print("Daily real iniciado.")
        print(f"Sindicatos candidatos com CNPJ válido: {len(rows)}")
        print("Modo: consultar MTE, comparar com banco e alertar somente novidades.")
        print("")

        for idx, row in enumerate(rows, start=1):
            name = row.get("nome") or ""
            cnpj = row.get("cnpj") or ""
            ufs = target_ufs(cfg, row)

            print(f"[{idx}/{len(rows)}] {name}")
            print(f"CNPJ: {cnpj} | UFs: {', '.join(ufs)}")

            for uf in ufs:
                print(f"  UF: {uf}")

                for typ in INSTRUMENT_TYPES.keys():
                    total_queries += 1

                    try:
                        result = client.search(
                            cnpj=cnpj,
                            uf=uf,
                            instrument_type=typ,
                        )

                        status = result.get("status")
                        items = result.get("items") or []

                        print(f"    - {typ}: {status} | coletados: {len(items)}")

                        total_seen += len(items)

                        for item in items:
                            item["sindicato_nome"] = name
                            item["tipo_instrumento"] = item.get("tipo_instrumento") or typ
                            item["tipo_descricao"] = INSTRUMENT_TYPES.get(
                                item.get("tipo_instrumento"),
                                item.get("tipo_instrumento") or "",
                            )

                            key = (
                                item.get("numero_registro") or "",
                                item.get("numero_solicitacao") or "",
                                item.get("tipo_instrumento") or "",
                            )

                            if key in seen_keys:
                                total_existing += 1
                                continue

                            seen_keys.add(key)

                            exists = db.instrument_exists(item)

                            if exists:
                                total_existing += 1
                                continue

                            if not should_alert_item(cfg, item):
                                total_ignored_old += 1

                                if not no_send:
                                    db.save_mte_instrument(
                                        item,
                                        sindicato_nome=name,
                                        known_before=True,
                                    )

                                continue

                            if no_send:
                                total_new += 1
                                new_items.append(item)
                                continue

                            inst_id, was_new = db.save_mte_instrument(
                                item,
                                sindicato_nome=name,
                                known_before=False,
                            )

                            if not was_new:
                                total_existing += 1
                                continue

                            total_new += 1

                            file_path = ""

                            if not no_download:
                                try:
                                    file_path = download_daily_doc(client, cfg, item)

                                    if file_path:
                                        db.set_instrument_file(inst_id, file_path)
                                        attachments.append(file_path)
                                        total_downloaded += 1

                                except Exception as err:
                                    errors.append(
                                        f"Falha ao baixar documento "
                                        f"{item.get('numero_registro')} / "
                                        f"{item.get('numero_solicitacao')}: {err}"
                                    )

                            subject = (
                                "Novo instrumento coletivo identificado - "
                                f"{item.get('numero_registro') or item.get('numero_solicitacao') or 'sem número'}"
                            )

                            alert_id, created = db.create_alert(
                                inst_id,
                                subject,
                                recipients=sec(cfg, "email").get("to", []),
                                attachments=[file_path] if file_path else [],
                            )

                            if alert_id:
                                alert_ids.append(alert_id)

                            new_items.append(item)

                    except Exception as err:
                        errors.append(f"{name} | {cnpj} | {uf} | {typ}: {err}")
                        print(f"    - {typ}: ERRO | {err}")

            print("")

        print("Daily concluído.")
        print(f"Consultas realizadas: {total_queries}")
        print(f"Instrumentos coletados: {total_seen}")
        print(f"Já existentes no banco: {total_existing}")
        print(f"Novos instrumentos: {total_new}")
        print(f"Ignorados por ano antigo/sem ano: {total_ignored_old}")
        print(f"Documentos baixados: {total_downloaded}")
        print(f"Erros: {len(errors)}")
        print("")

        finished_at = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

        stats = {
            "total_queries": total_queries,
            "total_seen": total_seen,
            "total_existing": total_existing,
            "total_new": total_new,
            "total_ignored_old": total_ignored_old,
            "total_downloaded": total_downloaded,
        }

        body = build_daily_body(
            new_items,
            errors,
            finished_at=finished_at,
            stats=stats,
        )

        send_empty = bool(sec(cfg, "email").get("send_when_empty", False))

        if not new_items and not send_empty:
            print("Nenhuma novidade encontrada. E-mail não enviado.")
            return

        if no_send:
            print("Modo --no-send ativo. E-mail não enviado.")
            print("")
            print(body)
            return

        max_new_items = int(sec(cfg, "email").get("max_new_items_to_send", 50))

        if new_items and len(new_items) > max_new_items:
            print(
                f"Envio bloqueado por segurança: {len(new_items)} novos instrumentos "
                f"identificados, acima do limite de {max_new_items}."
            )
            print("Isso normalmente indica baseline incompleto ou mudança de parâmetro de busca.")
            print("Os alertas foram criados no banco, mas o e-mail não foi enviado.")
            print("Revise a base antes de enviar.")
            return

        result = Emailer(cfg).send(
            "Monitoramento diário de instrumentos coletivos",
            body,
            attachments=attachments,
        )

        if result.get("dry_run"):
            print("Dry-run ativo. E-mail gravado em:", result["path"])
        else:
            db.mark_alerts_sent(alert_ids)
            print("E-mail de monitoramento enviado com sucesso.")

    except Exception as err:
        try:
            db.mark_alerts_error(alert_ids, err)
        except Exception:
            pass

        raise

    finally:
        db.close()

def target_ufs(cfg, row=None):
    row = row or {}

    monitor = sec(cfg, "monitor")
    cfg_ufs = monitor.get("ufs", [])

    clean = []

    for uf in cfg_ufs:
        uf = str(uf or "").strip().upper()

        if uf and uf not in clean:
            clean.append(uf)

    if clean:
        return clean

    inferred = str(row.get("uf_inferida") or "").strip().upper()

    if inferred:
        return [inferred]

    return ["DF", "GO"]

def registration_year(item):
    reg = str(item.get("numero_registro") or "")

    match = re.search(r"/(\d{4})", reg)
    if match:
        return int(match.group(1))

    date = str(item.get("data_registro") or "")
    match = re.search(r"(\d{4})", date)
    if match:
        return int(match.group(1))

    return None


def should_alert_item(cfg, item):
    monitor = sec(cfg, "monitor")

    min_year = int(
        monitor.get(
            "min_registration_year_to_alert",
            datetime.now().year - 1,
        )
    )

    alert_without_year = bool(
        monitor.get(
            "alert_without_registration_year",
            False,
        )
    )

    year = registration_year(item)

    if year is None:
        return alert_without_year

    return year >= min_year

def registration_year(item):
    reg = str(item.get("numero_registro") or "")

    match = re.search(r"/(\d{4})", reg)
    if match:
        return int(match.group(1))

    date = str(item.get("data_registro") or "")
    match = re.search(r"(\d{4})", date)
    if match:
        return int(match.group(1))

    return None


def should_alert_item(cfg, item):
    monitor = sec(cfg, "monitor")

    min_year = int(monitor.get("min_registration_year_to_alert", datetime.now().year - 1))
    alert_without_year = bool(monitor.get("alert_without_registration_year", False))

    year = registration_year(item)

    if year is None:
        return alert_without_year

    return year >= min_year

def main():
    parser = argparse.ArgumentParser(prog="Odysséus")
    parser.add_argument("--config", default="config.toml")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check-db", help="Confere se o banco SQLite está acessível e mostra contagens.")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("export-candidates", help="Exporta sindicatos candidatos ao monitoramento.")
    p.add_argument("--include-no-cnpj", action="store_true", help="Inclui registros sem CNPJ válido.")
    p.set_defaults(func=cmd_export_candidates)

    p = sub.add_parser("baseline-summary", help="Exporta resumo da base manual já conhecida.")
    p.set_defaults(func=cmd_baseline_summary)

    p = sub.add_parser("email-test", help="Testa o envio de e-mail.")
    p.add_argument("--create-alert", action="store_true", help="Cria um alerta fictício no banco antes do teste.")
    p.set_defaults(func=cmd_email_test)

    p = sub.add_parser("seed-baseline", help="Cria a base inicial de instrumentos já conhecidos, sem disparar e-mail.")
    p.add_argument("--limit", type=int, default=0, help="Limita a quantidade de sindicatos para teste.")
    p.set_defaults(func=cmd_seed_baseline)

    p = sub.add_parser("mte-test", help="Testa uma consulta real no Mediador/MTE.")
    p.add_argument("--cnpj", default="", help="CNPJ do sindicato.")
    p.add_argument("--uf", default="DF", help="UF de registro/abrangência. Ex: DF ou GO.")
    p.add_argument(
        "--type",
        default="convencao",
        choices=list(INSTRUMENT_TYPES.keys()),
        help="Tipo do instrumento coletivo.",
    )
    p.add_argument("--first", action="store_true", help="Usa o primeiro sindicato candidato do banco.")
    p.add_argument("--raw", action="store_true", help="Mostra trecho bruto do retorno do MTE.")
    p.set_defaults(func=cmd_mte_test)

    p = sub.add_parser("daily", help="Executa o monitoramento diário real do Mediador/MTE.")
    p.add_argument("--limit", type=int, default=0, help="Limita a quantidade de sindicatos para teste.")
    p.add_argument("--no-send", action="store_true", help="Executa a busca, mas não envia e-mail.")
    p.add_argument("--no-download", action="store_true", help="Não baixa documentos encontrados.")
    p.set_defaults(func=cmd_daily)

    args = parser.parse_args()
    args.func(args)