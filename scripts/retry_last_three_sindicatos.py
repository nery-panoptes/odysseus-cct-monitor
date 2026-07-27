#!/usr/bin/env python3
from pathlib import Path
import sys

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
    target_ufs,
)
from odysseus.emailer import Emailer
from odysseus.mte import INSTRUMENT_TYPES, MteClient
from odysseus.office import apply_company_filter, refresh_office_companies


def last_three_targets(db):
    rows = db.con.execute("""
        select
            i.sindicato_cnpj,
            i.sindicato_nome,
            i.uf
        from alertas_email a
        join instrumentos_mte i on i.id = a.instrumento_id
        where a.enviado = 1
          and a.enviado_em = (
              select max(enviado_em)
              from alertas_email
              where enviado = 1
                and coalesce(enviado_em, '') != ''
          )
        order by a.id desc
    """).fetchall()

    targets = []
    seen = set()

    for row in rows:
        cnpj = row["sindicato_cnpj"] or ""

        if not cnpj or cnpj in seen:
            continue

        seen.add(cnpj)
        targets.append({
            "cnpj": cnpj,
            "nome": row["sindicato_nome"] or "",
            "uf_inferida": row["uf"] or "",
        })

        if len(targets) >= 3:
            break

    return targets


def main():
    cfg = loadcfg(PROJECT_ROOT / "config.toml")
    db = opendb(cfg)

    total_queries = 0
    total_seen = 0
    total_existing = 0
    total_new = 0
    total_downloaded = 0
    total_ignored_old = 0
    total_filtered_company = 0

    errors = []
    new_items = []
    attachments = []
    alert_ids = []
    seen_keys = set()

    try:
        office_companies = refresh_office_companies(cfg, db)
        targets = last_three_targets(db)

        if not targets:
            raise RuntimeError("Não encontrei últimos alertas enviados no banco.")

        client = MteClient(cfg)

        print("Reconsulta completa dos últimos sindicatos enviados.")
        print(f"Total de sindicatos distintos: {len(targets)}")
        print("")

        for idx, row in enumerate(targets, start=1):
            name = row.get("nome") or ""
            cnpj = row.get("cnpj") or ""
            ufs = target_ufs(cfg, row)

            print(f"[{idx}/{len(targets)}] {name}")
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

                        if not result.get("ok", True):
                            errors.append(
                                f"{name} | {cnpj} | {uf} | {typ}: "
                                f"{result.get('message') or status}"
                            )
                            continue

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

                            if db.instrument_exists(item):
                                total_existing += 1
                                continue

                            if not should_alert_item(cfg, item):
                                total_ignored_old += 1
                                db.save_mte_instrument(
                                    item,
                                    sindicato_nome=name,
                                    known_before=True,
                                )
                                continue

                            inst_id, was_new = db.save_mte_instrument(
                                item,
                                sindicato_nome=name,
                                known_before=False,
                            )

                            if not was_new:
                                total_existing += 1
                                continue

                            file_path = ""

                            try:
                                file_path = download_daily_doc(client, cfg, item)

                                if file_path:
                                    db.set_instrument_file(inst_id, file_path)
                                    total_downloaded += 1

                            except Exception as err:
                                errors.append(
                                    f"Falha ao baixar/resumir "
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
                                summarize_daily_doc(db, cfg, item, inst_id, file_path)

                            alert_id, _ = db.create_alert(
                                inst_id,
                                "Reconsulta dos últimos sindicatos - "
                                f"{item.get('numero_registro') or item.get('numero_solicitacao') or 'sem número'}",
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

        stats = {
            "total_queries": total_queries,
            "total_seen": total_seen,
            "total_existing": total_existing,
            "total_new": total_new,
            "total_ignored_old": total_ignored_old,
            "total_filtered_company": total_filtered_company,
            "total_downloaded": total_downloaded,
        }

        body = build_daily_body(new_items, errors, stats=stats)

        result = Emailer(cfg).send(
            "Reconsulta completa dos últimos sindicatos",
            body,
            attachments=attachments,
        )

        if result.get("dry_run"):
            print("Dry-run ativo. E-mail gravado em:", result["path"])
        else:
            db.mark_alerts_sent(alert_ids)
            print("E-mail de reconsulta enviado com sucesso.")

        print("")
        print("Resumo:")
        print(f"- Consultas realizadas: {total_queries}")
        print(f"- Instrumentos coletados: {total_seen}")
        print(f"- Já existentes: {total_existing}")
        print(f"- Novos instrumentos: {total_new}")
        print(f"- Ignorados por empresa fora da base: {total_filtered_company}")
        print(f"- Documentos baixados: {total_downloaded}")
        print(f"- Erros: {len(errors)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
