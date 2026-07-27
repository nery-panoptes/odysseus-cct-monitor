#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from odysseus.cfg import loadcfg, sec
from odysseus.cli import (
    build_daily_body,
    download_daily_doc,
    opendb,
    should_alert_item,
    summarize_daily_doc,
)
from odysseus.emailer import Emailer
from odysseus.mte import INSTRUMENT_TYPES, MteClient
from odysseus.office import apply_company_filter, refresh_office_companies


def latest_log(base):
    logs_dir = base / "logs"
    logs = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not logs:
        raise RuntimeError(f"Nenhum log encontrado em {logs_dir}.")

    return logs[0]


def parse_failed_queries(log_path):
    failures = []
    current_name = ""
    current_cnpj = ""
    current_uf = ""

    cnpj_re = re.compile(r"CNPJ:\s*([0-9./-]+)\s*\|")
    item_re = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+)$")
    uf_re = re.compile(r"^\s+UF:\s*([A-Z]{2})\s*$")
    error_re = re.compile(r"^\s+-\s+(\w+):\s+ERRO\s+\|\s+(.+)$")

    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        item_match = item_re.match(line)

        if item_match:
            current_name = item_match.group(3).strip()
            current_cnpj = ""
            current_uf = ""
            continue

        cnpj_match = cnpj_re.search(line)

        if cnpj_match:
            current_cnpj = re.sub(r"\D+", "", cnpj_match.group(1))
            continue

        uf_match = uf_re.match(line)

        if uf_match:
            current_uf = uf_match.group(1)
            continue

        error_match = error_re.match(line)

        if error_match and current_cnpj and current_uf:
            failures.append({
                "sindicato_nome": current_name,
                "cnpj": current_cnpj,
                "uf": current_uf,
                "tipo": error_match.group(1),
                "erro": error_match.group(2).strip(),
            })

    unique = []
    seen = set()

    for failure in failures:
        key = (failure["cnpj"], failure["uf"], failure["tipo"])

        if key in seen:
            continue

        seen.add(key)
        unique.append(failure)

    return unique


def latest_sent_batch_missing_summaries(db):
    row = db.con.execute("""
        select max(enviado_em) as enviado_em
        from alertas_email
        where enviado = 1
          and coalesce(enviado_em, '') != ''
    """).fetchone()

    if not row or not row["enviado_em"]:
        return []

    rows = db.con.execute("""
        select
            a.id as alert_id,
            i.*
        from alertas_email a
        join instrumentos_mte i on i.id = a.instrumento_id
        where a.enviado = 1
          and a.enviado_em = ?
          and coalesce(i.arquivo_path, '') != ''
          and coalesce(i.resumo_status, '') = ''
        order by a.id
    """, (row["enviado_em"],)).fetchall()

    return [dict(item) for item in rows]


def latest_sent_batch(db):
    row = db.con.execute("""
        select max(enviado_em) as enviado_em
        from alertas_email
        where enviado = 1
          and coalesce(enviado_em, '') != ''
    """).fetchone()

    if not row or not row["enviado_em"]:
        return []

    rows = db.con.execute("""
        select
            a.id as alert_id,
            i.*
        from alertas_email a
        join instrumentos_mte i on i.id = a.instrumento_id
        where a.enviado = 1
          and a.enviado_em = ?
        order by a.id
    """, (row["enviado_em"],)).fetchall()

    return [dict(item) for item in rows]


def process_item(db, cfg, client, item, attachments, office_companies):
    inst_id, was_new = db.save_mte_instrument(
        item,
        sindicato_nome=item.get("sindicato_nome") or "",
        known_before=False,
    )

    if not inst_id:
        return None

    file_path = item.get("arquivo_path") or ""

    if not file_path:
        file_path = download_daily_doc(client, cfg, item)
        db.set_instrument_file(inst_id, file_path)

    try:
        company_decision = apply_company_filter(
            db,
            cfg,
            item,
            inst_id,
            file_path,
            office_companies,
        )
    except Exception:
        company_decision = {"should_alert": True}

    if not company_decision.get("should_alert", True):
        item["_company_filtered"] = True
        print(
            "  ignorado: empresa do acordo fora da base do escritório "
            f"({item.get('numero_registro') or item.get('numero_solicitacao')})"
        )
        return None

    if file_path:
        attachments.append(file_path)
        summarize_daily_doc(db, cfg, item, inst_id, file_path)

    db.create_alert(
        inst_id,
        "Reprocessamento de instrumento coletivo - "
        f"{item.get('numero_registro') or item.get('numero_solicitacao') or 'sem número'}",
        recipients=sec(cfg, "email").get("to", []),
        attachments=[file_path] if file_path else [],
    )

    return item if was_new else None


def retry_failed_queries(db, cfg, failures):
    client = MteClient(cfg)
    office_companies = refresh_office_companies(cfg, db)
    new_items = []
    attachments = []
    errors = []
    stats = {
        "total_queries": 0,
        "total_seen": 0,
        "total_existing": 0,
        "total_new": 0,
        "total_ignored_old": 0,
        "total_filtered_company": 0,
        "total_downloaded": 0,
    }

    for failure in failures:
        stats["total_queries"] += 1
        print(
            "Reconsultando: "
            f"{failure['sindicato_nome']} | {failure['cnpj']} | "
            f"{failure['uf']} | {failure['tipo']}"
        )

        try:
            result = client.search(
                cnpj=failure["cnpj"],
                uf=failure["uf"],
                instrument_type=failure["tipo"],
            )
        except Exception as err:
            errors.append(f"{failure['cnpj']} | {failure['uf']} | {failure['tipo']}: {err}")
            continue

        status = result.get("status")
        items = result.get("items") or []
        print(f"  status: {status} | coletados: {len(items)}")

        if not result.get("ok", True):
            errors.append(
                f"{failure['cnpj']} | {failure['uf']} | {failure['tipo']}: "
                f"{result.get('message') or status}"
            )
            continue

        stats["total_seen"] += len(items)

        for item in items:
            item["sindicato_nome"] = failure["sindicato_nome"]
            item["tipo_instrumento"] = item.get("tipo_instrumento") or failure["tipo"]
            item["tipo_descricao"] = INSTRUMENT_TYPES.get(
                item.get("tipo_instrumento"),
                item.get("tipo_instrumento") or "",
            )

            if db.instrument_exists(item):
                stats["total_existing"] += 1
                continue

            if not should_alert_item(cfg, item):
                stats["total_ignored_old"] += 1
                db.save_mte_instrument(item, sindicato_nome=item["sindicato_nome"], known_before=True)
                continue

            before_downloads = len(attachments)
            processed = process_item(db, cfg, client, item, attachments, office_companies)

            if item.pop("_company_filtered", False):
                stats["total_filtered_company"] += 1
            elif processed:
                new_items.append(processed)
                stats["total_new"] += 1

            stats["total_downloaded"] += len(attachments) - before_downloads

    return new_items, attachments, errors, stats


def reprocess_missing_summaries(db, cfg, missing):
    recovered = []
    attachments = []
    errors = []

    for row in missing:
        file_path = row.get("arquivo_path") or ""
        print(
            "Reprocessando resumo: "
            f"{row.get('numero_registro') or row.get('numero_solicitacao')} | {file_path}"
        )

        try:
            summarize_daily_doc(db, cfg, row, row["id"], file_path)
            refreshed = db.con.execute(
                "select * from instrumentos_mte where id = ?",
                (row["id"],),
            ).fetchone()
            recovered.append(dict(refreshed))
            attachments.append(file_path)
        except Exception as err:
            errors.append(
                f"{row.get('numero_registro')} / {row.get('numero_solicitacao')}: {err}"
            )

    return recovered, attachments, errors


def recovered_body(recovered, errors):
    lines = [
        "Bom dia,",
        "",
        "Odysséus reprocessou ocorrências da última execução diária.",
        "",
    ]

    if recovered:
        lines.append(f"Resumo recuperado para {len(recovered)} instrumento(s):")
        lines.append("")

        for item in recovered:
            lines.append(f"- Sindicato: {item.get('sindicato_nome') or ''}")
            lines.append(f"  Tipo: {item.get('tipo_instrumento') or ''}")
            lines.append(f"  Registro: {item.get('numero_registro') or ''}")
            lines.append(f"  Solicitação: {item.get('numero_solicitacao') or ''}")

            summary = str(item.get("resumo_mudancas") or "").strip()

            if summary:
                lines.append("  Resumo automático:")

                for line in summary.splitlines():
                    if line.strip():
                        lines.append(f"  - {line.strip()}")

            lines.append("")

    if errors:
        lines.append("Ocorrências que permaneceram com erro:")

        for error in errors:
            lines.append(f"- {error}")

        lines.append("")

    lines.extend([
        "Atenciosamente,",
        "Odysséus, Robô de Monitoramento de Convenções Coletivas",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Reexecuta somente ocorrências com erro da última execução diária."
    )
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.toml"))
    parser.add_argument("--log", default="", help="Log específico. Se omitido, usa o log mais recente.")
    parser.add_argument("--send-email", action="store_true", help="Envia e-mail de follow-up com o resultado.")
    parser.add_argument(
        "--send-latest-batch",
        action="store_true",
        help="No follow-up, inclui todos os instrumentos da última leva enviada.",
    )
    args = parser.parse_args()

    cfg = loadcfg(args.config)
    base = Path(cfg["base"])
    log_path = Path(args.log) if args.log else latest_log(base)

    db = opendb(cfg)

    try:
        failures = parse_failed_queries(log_path)
        missing = latest_sent_batch_missing_summaries(db)

        print(f"Log analisado: {log_path}")
        print(f"Consultas com ERRO no log: {len(failures)}")
        print(f"Instrumentos da última leva sem resumo: {len(missing)}")
        print("")

        new_items, query_attachments, query_errors, stats = retry_failed_queries(db, cfg, failures)
        recovered, recovered_attachments, summary_errors = reprocess_missing_summaries(db, cfg, missing)
        all_errors = query_errors + summary_errors
        followup_items = recovered
        followup_attachments = recovered_attachments

        if args.send_latest_batch:
            followup_items = latest_sent_batch(db)
            followup_attachments = [
                item.get("arquivo_path") or ""
                for item in followup_items
                if item.get("arquivo_path")
            ]

        print("")
        print("Resultado do reprocessamento:")
        print(f"- Novos instrumentos encontrados nas consultas refeitas: {len(new_items)}")
        print(f"- Resumos recuperados: {len(recovered)}")
        print(f"- Erros restantes: {len(all_errors)}")

        if all_errors:
            for error in all_errors:
                print(f"  - {error}")

        if args.send_email and (new_items or followup_items or all_errors):
            attachments = query_attachments + followup_attachments

            if new_items:
                body = build_daily_body(new_items, all_errors, stats=stats)
            else:
                body = recovered_body(followup_items, all_errors)

            result = Emailer(cfg).send(
                "Reprocessamento de ocorrências do monitoramento diário",
                body,
                attachments=attachments,
            )

            if result.get("dry_run"):
                print("Dry-run ativo. E-mail gravado em:", result["path"])
            else:
                print("E-mail de follow-up enviado com sucesso.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
