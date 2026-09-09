import argparse
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .cfg import loadcfg, sec
from .db import Db
from .emailer import Emailer
from .mte import DOWNLOAD_URL, INSTRUMENT_TYPES, MteClient
from .office import (
    apply_company_filter,
    format_company_matches,
    load_monitor_candidates,
    monitor_name_filter_report,
    refresh_office_companies,
)
from .health import collect_health_report, format_health_text, write_health_html
from .report import write_csv
from .summarizer import build_document_summary, detect_file_type, summary_enabled
from .util import root


class FatalMteBlock(RuntimeError):
    pass


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


def cmd_office_companies_check(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        refresh_office_companies(cfg, db)
        rows = db.office_companies(document_type="", only_active=False)
        cnpjs = [row for row in rows if row.get("tipo_documento") == "cnpj"]
        cpfs = [row for row in rows if row.get("tipo_documento") == "cpf"]

        print("Base de empresas do escritório atualizada.")
        print(f"- Registros totais: {len(rows)}")
        print(f"- CNPJs: {len(cnpjs)}")
        print(f"- CPFs: {len(cpfs)}")
        print("")

        for row in cnpjs[:10]:
            print(f"- {row.get('razao_social') or '(sem razão social)'} | {row.get('documento')}")

        if len(cnpjs) > 10:
            print(f"- ... mais {len(cnpjs) - 10} CNPJ(s).")

    finally:
        db.close()


def cmd_monitor_source_check(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        source = str(sec(cfg, "monitor_source").get("source", "database") or "database")

        if source.lower() in {"google_sheet_filter", "google_sheet_name_filter", "name_filter"}:
            report = monitor_name_filter_report(cfg, db, only_with_cnpj=True)

            print("Fonte online de sindicatos conferida.")
            print(f"- Coluna usada: {report['filter_column']}")
            print(f"- URL CSV: {report['csv_url']}")
            print(f"- Nomes unicos na planilha: {report['total_sheet_names']}")
            print(f"- Candidatos com CNPJ antes do filtro: {report['total_candidates_before']}")
            print(f"- Candidatos apos filtro DF/GO: {report['total_candidates_after_uf_scope']}")
            print(f"- Candidatos que serao consultados: {report['total_candidates_after']}")
            print("")

            print("Primeiros candidatos que serao consultados:")

            for row in report["candidates"][:20]:
                print(f"- {row.get('nome') or ''} | {row.get('cnpj') or ''} | UF {row.get('uf_inferida') or ''}")

            if len(report["candidates"]) > 20:
                print(f"- ... mais {len(report['candidates']) - 20} candidato(s).")

            if report["unmatched_names"]:
                print("")
                print("Nomes da planilha sem CNPJ correspondente no cadastro local:")

                for name in report["unmatched_names"][:30]:
                    print(f"- {name}")

                if len(report["unmatched_names"]) > 30:
                    print(f"- ... mais {len(report['unmatched_names']) - 30} nome(s).")

            return

        rows, monitor_source = load_monitor_candidates(cfg, db, only_with_cnpj=True)
        print(f"Fonte dos sindicatos: {monitor_source}")
        print(f"Candidatos com CNPJ: {len(rows)}")

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


def cmd_health_report(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)

    try:
        report = collect_health_report(db, cfg, days=args.days)
    finally:
        db.close()

    body = format_health_text(report)
    print(body)

    html_path = None

    if args.html or args.output or args.send:
        html_path = write_health_html(cfg, report, output=args.output)
        print("")
        print(f"Relatório HTML gerado em: {html_path}")

    if args.send:
        attachments = [str(html_path)] if html_path else []
        result = Emailer(cfg).send(
            "Health Report do Odysséus",
            body,
            attachments=attachments,
        )

        if result.get("dry_run"):
            print("Dry-run ativo. E-mail do Health Report gravado em:", result["path"])
        else:
            print("E-mail do Health Report enviado com sucesso.")


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

            company_matches = format_company_matches(item.get("empresas_escritorio_match") or [])

            if company_matches:
                lines.append(f"  Empresas do escritório relacionadas: {company_matches}")

            if item.get("url_documento"):
                lines.append(f"  Link: {item.get('url_documento')}")

            summary = str(item.get("resumo_mudancas") or "").strip()

            if summary:
                lines.append("  Resumo automático das mudanças:")

                for summary_line in summary.splitlines():
                    summary_line = summary_line.strip()

                    if summary_line:
                        lines.append(f"  - {summary_line}")

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
        lines.append(f"- Acordos ignorados por empresa fora da base: {stats.get('total_filtered_company', 0)}")
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

    detected = detect_file_type(path)
    detected_ext = detected.get("extension") or "doc"

    if detected_ext and detected_ext != path.suffix.lstrip(".").lower():
        new_path = path.with_suffix(f".{detected_ext}")
        path.replace(new_path)
        path = new_path

    return str(path)


def summarize_daily_doc(db, cfg, item, instrumento_id, file_path):
    if not summary_enabled(cfg) or not file_path:
        return ""

    previous = db.previous_instrument(item, current_id=instrumento_id)
    previous_path = ""

    if previous:
        previous_path = previous.get("arquivo_path") or ""

    result = build_document_summary(
        file_path,
        item=item,
        previous_path=previous_path,
        cfg=cfg,
    )

    summary = result.get("summary") or ""

    item["resumo_mudancas"] = summary
    item["arquivo_tipo_detectado"] = result.get("file_type") or ""
    item["ocr_necessario"] = result.get("ocr_needed") or False

    db.set_instrument_summary(
        instrumento_id,
        summary,
        status=result.get("status") or "",
        file_type=result.get("file_type") or "",
        ocr_needed=result.get("ocr_needed") or False,
    )

    return summary

def cmd_daily(args):
    cfg = loadcfg(args.config)
    db = opendb(cfg)
    run_id = None

    limit = getattr(args, "limit", None)
    no_send = getattr(args, "no_send", False)
    no_download = getattr(args, "no_download", False)

    total_queries = 0
    total_seen = 0
    total_existing = 0
    total_new = 0
    total_downloaded = 0
    total_ignored_old = 0
    total_filtered_company = 0
    total_alerts_created = 0
    total_alerts_sent = 0
    blocked_hits = 0
    blocked_abort_after = int(sec(cfg, "mte").get("blocked_abort_after", 2))

    errors = []
    new_items = []
    alert_ids = []
    attachments = []
    seen_keys = set()

    def current_stats():
        return {
            "total_queries": total_queries,
            "total_seen": total_seen,
            "total_existing": total_existing,
            "total_new": total_new,
            "total_ignored_old": total_ignored_old,
            "total_filtered_company": total_filtered_company,
            "total_downloaded": total_downloaded,
            "total_errors": len(errors),
            "total_alerts_created": total_alerts_created,
            "total_alerts_sent": total_alerts_sent,
        }

    try:
        run_id = db.begin_monitor_run()

        if bool(sec(cfg, "office_companies").get("refresh_on_daily", True)):
            office_companies = refresh_office_companies(cfg, db)
        else:
            office_companies = db.office_companies(document_type="cnpj")

        rows, monitor_source = load_monitor_candidates(cfg, db, only_with_cnpj=True)

        if limit:
            rows = rows[:limit]

        db.update_monitor_run_context(
            run_id,
            total_sindicatos=len(rows),
            monitor_source=monitor_source,
            total_empresas=len(office_companies),
        )

        client = MteClient(cfg)

        print("Daily real iniciado.")
        print(f"Sindicatos candidatos com CNPJ válido: {len(rows)}")
        print(f"Fonte dos sindicatos: {monitor_source}")
        print(f"Empresas com CNPJ na base do escritório: {len(office_companies)}")
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
                    query_started = time.perf_counter()

                    try:
                        result = client.search(
                            cnpj=cnpj,
                            uf=uf,
                            instrument_type=typ,
                        )
                        elapsed_ms = int((time.perf_counter() - query_started) * 1000)

                        status = result.get("status")
                        items = result.get("items") or []

                        print(f"    - {typ}: {status} | coletados: {len(items)}")

                        db.record_mte_query(
                            run_id,
                            cnpj,
                            name,
                            uf,
                            typ,
                            status,
                            http_code=result.get("http_code") or "",
                            message=result.get("message") or "",
                            elapsed_ms=elapsed_ms,
                        )

                        if not result.get("ok", True):
                            message = result.get("message") or "Consulta recusada pelo MTE."
                            http_code = result.get("http_code") or ""
                            err = (
                                f"{name} | {cnpj} | {uf} | {typ}: "
                                f"{message} (status: {status}, HTTP {http_code})"
                            )
                            errors.append(err)

                            if status == "captcha_or_blocked":
                                blocked_hits += 1

                                if blocked_hits >= blocked_abort_after:
                                    raise FatalMteBlock(
                                        "MTE bloqueou as consultas por desafio de navegador/cookies. "
                                        "Execução interrompida para evitar varredura inútil e e-mail incorreto. "
                                        f"Última ocorrência: {message}"
                                    )

                            continue

                        blocked_hits = 0
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

                            inst_id = None
                            was_new = True

                            if not no_send:
                                inst_id, was_new = db.save_mte_instrument(
                                    item,
                                    sindicato_nome=name,
                                    known_before=False,
                                )

                                if not was_new:
                                    total_existing += 1
                                    continue


                            file_path = ""

                            if not no_download:
                                try:
                                    file_path = download_daily_doc(client, cfg, item)

                                    if file_path:
                                        if inst_id:
                                            db.set_instrument_file(inst_id, file_path)

                                        total_downloaded += 1

                                except Exception as err:
                                    errors.append(
                                        f"Falha ao baixar documento "
                                        f"{item.get('numero_registro')} / "
                                        f"{item.get('numero_solicitacao')}: {err}"
                                    )

                            try:
                                company_decision = apply_company_filter(
                                    db,
                                    cfg,
                                    item,
                                    inst_id,
                                    file_path,
                                    office_companies,
                                )
                            except Exception as err:
                                company_decision = {"should_alert": True}
                                errors.append(
                                    f"Falha ao aplicar filtro de empresas "
                                    f"{item.get('numero_registro')} / "
                                    f"{item.get('numero_solicitacao')}: {err}"
                                )

                            if not company_decision.get("should_alert", True):
                                total_filtered_company += 1
                                print(
                                    "      ignorado: empresa do acordo fora da base do escritório "
                                    f"({item.get('numero_registro') or item.get('numero_solicitacao')})"
                                )
                                continue

                            total_new += 1

                            if file_path:
                                attachments.append(file_path)

                            if inst_id and file_path:
                                try:
                                    summarize_daily_doc(
                                        db,
                                        cfg,
                                        item,
                                        inst_id,
                                        file_path,
                                    )
                                except Exception as err:
                                    errors.append(
                                        f"Falha ao gerar resumo automático "
                                        f"{item.get('numero_registro')} / "
                                        f"{item.get('numero_solicitacao')}: {err}"
                                    )

                            subject = (
                                "Novo instrumento coletivo identificado - "
                                f"{item.get('numero_registro') or item.get('numero_solicitacao') or 'sem número'}"
                            )

                            if not no_send:
                                alert_id, created = db.create_alert(
                                    inst_id,
                                    subject,
                                    recipients=sec(cfg, "email").get("to", []),
                                    attachments=[file_path] if file_path else [],
                                )

                                if alert_id:
                                    alert_ids.append(alert_id)

                                if created:
                                    total_alerts_created += 1

                            new_items.append(item)

                    except FatalMteBlock:
                        raise

                    except Exception as err:
                        errors.append(f"{name} | {cnpj} | {uf} | {typ}: {err}")
                        print(f"    - {typ}: ERRO | {err}")
                        elapsed_ms = int((time.perf_counter() - query_started) * 1000)
                        db.record_mte_query(
                            run_id,
                            cnpj,
                            name,
                            uf,
                            typ,
                            "exception",
                            message=str(err),
                            elapsed_ms=elapsed_ms,
                        )

            print("")

        print("Daily concluído.")
        print(f"Consultas realizadas: {total_queries}")
        print(f"Instrumentos coletados: {total_seen}")
        print(f"Já existentes no banco: {total_existing}")
        print(f"Novos instrumentos: {total_new}")
        print(f"Ignorados por ano antigo/sem ano: {total_ignored_old}")
        print(f"Ignorados por empresa fora da base: {total_filtered_company}")
        print(f"Documentos baixados: {total_downloaded}")
        print(f"Erros: {len(errors)}")
        print("")

        finished_at = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

        stats = current_stats()

        body = build_daily_body(
            new_items,
            errors,
            finished_at=finished_at,
            stats=stats,
        )

        send_empty = bool(sec(cfg, "email").get("send_when_empty", False))

        if not new_items and not send_empty:
            print("Nenhuma novidade encontrada. E-mail não enviado.")
            db.finish_monitor_run(run_id, "success_no_email", current_stats())
            return

        if no_send:
            print("Modo --no-send ativo. E-mail não enviado.")
            print("")
            print(body)
            db.finish_monitor_run(run_id, "no_send", current_stats())
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
            db.finish_monitor_run(
                run_id,
                "email_blocked_safety",
                current_stats(),
                "Quantidade de novos instrumentos acima do limite configurado.",
            )
            return

        result = Emailer(cfg).send(
            "Monitoramento diário de instrumentos coletivos",
            body,
            attachments=attachments,
        )

        if result.get("dry_run"):
            print("Dry-run ativo. E-mail gravado em:", result["path"])
            db.finish_monitor_run(run_id, "dry_run", current_stats())
        else:
            db.mark_alerts_sent(alert_ids)
            total_alerts_sent = len(alert_ids)
            db.finish_monitor_run(run_id, "success", current_stats())
            print("E-mail de monitoramento enviado com sucesso.")

    except FatalMteBlock as err:
        try:
            db.mark_alerts_error(alert_ids, err)
        except Exception:
            pass

        print("")
        print(f"Execução interrompida: {err}")
        print("Nenhum e-mail de monitoramento foi enviado.")
        db.finish_monitor_run(run_id, "blocked", current_stats(), str(err))

    except Exception as err:
        try:
            db.mark_alerts_error(alert_ids, err)
        except Exception:
            pass

        db.finish_monitor_run(run_id, "failed", current_stats(), str(err))
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

    p = sub.add_parser("office-companies-check", help="Atualiza e confere a base de empresas do escritório.")
    p.set_defaults(func=cmd_office_companies_check)

    p = sub.add_parser("monitor-source-check", help="Confere a fonte configurada de sindicatos do monitoramento.")
    p.set_defaults(func=cmd_monitor_source_check)

    p = sub.add_parser("email-test", help="Testa o envio de e-mail.")
    p.add_argument("--create-alert", action="store_true", help="Cria um alerta fictício no banco antes do teste.")
    p.set_defaults(func=cmd_email_test)

    for command_name in ("health-report", "diagnose"):
        p = sub.add_parser(command_name, help="Gera diagnóstico operacional do Odysséus.")
        p.add_argument("--days", type=int, default=30, help="Quantidade de dias analisados.")
        p.add_argument("--html", action="store_true", help="Também gera um relatório HTML em reports/.")
        p.add_argument("--output", default="", help="Caminho do relatório HTML. Implica --html.")
        p.add_argument("--send", action="store_true", help="Envia o Health Report por e-mail.")
        p.set_defaults(func=cmd_health_report)

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
