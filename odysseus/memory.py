from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import re
import unicodedata

from .cfg import sec
from .summarizer import extract_text_from_file


STOPWORDS = {
    "ainda",
    "anos",
    "apos",
    "assim",
    "cada",
    "caso",
    "celebram",
    "coletivo",
    "coletiva",
    "com",
    "como",
    "contra",
    "das",
    "data",
    "de",
    "depois",
    "desde",
    "deste",
    "desta",
    "dias",
    "dos",
    "e",
    "em",
    "entre",
    "esse",
    "esta",
    "este",
    "fica",
    "fim",
    "foi",
    "forma",
    "foram",
    "geral",
    "instrumento",
    "instrumentos",
    "mediante",
    "meses",
    "na",
    "nas",
    "no",
    "nos",
    "neste",
    "nesta",
    "numero",
    "ou",
    "para",
    "pela",
    "pelo",
    "por",
    "presente",
    "que",
    "registro",
    "sera",
    "serao",
    "sobre",
    "sua",
    "suas",
    "todo",
    "todos",
    "trabalho",
}

SEND_ACTIONS = {"send_email", "email_sent", "email_pending", "manual_send"}
DISCARD_ACTIONS = {"discard_company", "discard_old", "manual_discard"}
REVIEW_ACTIONS = {"review_unknown", "manual_review"}


def memory_enabled(cfg):
    return bool(sec(cfg, "memory").get("enabled", True))


def _memory_cfg(cfg):
    return sec(cfg, "memory")


def _float_cfg(cfg, key, default):
    try:
        return float(_memory_cfg(cfg).get(key, default))
    except Exception:
        return float(default)


def _int_cfg(cfg, key, default):
    try:
        return int(_memory_cfg(cfg).get(key, default))
    except Exception:
        return int(default)


def _strip_accents(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _words(value):
    text = _strip_accents(value).lower()
    return re.findall(r"[a-z0-9]{4,}", text)


def _short(value, limit=180):
    text = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(text) <= limit:
        return text

    return text[: max(0, limit - 3)].rstrip() + "..."


def _json(value, default):
    if not value:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


def _since(days):
    days = max(1, int(days or 30))
    return (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def text_terms(text, max_terms=80):
    words = [
        word
        for word in _words(text)
        if word not in STOPWORDS and not word.isdigit()
    ]

    counter = Counter(words)
    return [word for word, _count in counter.most_common(max_terms)]


def text_fingerprint(terms):
    terms = sorted(set(terms or []))

    if not terms:
        return ""

    return hashlib.sha256(" ".join(terms).encode("utf-8")).hexdigest()[:24]


def _term_similarity(left, right):
    left_set = set(left or [])
    right_set = set(right or [])

    if not left_set or not right_set:
        return 0.0

    return len(left_set & right_set) / len(left_set | right_set)


def _action_family(action):
    action = str(action or "")

    if action in DISCARD_ACTIONS:
        return "discard"

    if action in SEND_ACTIONS:
        return "send"

    if action in REVIEW_ACTIONS:
        return "review"

    return "review"


def _decision_for_action(action, needs_review=False):
    if needs_review:
        return "review"

    return _action_family(action)


def _extract_file_signals(file_path, cfg, strict_file_check=True):
    info = {
        "ok": False,
        "text": "",
        "file_type": "",
        "ocr_needed": False,
        "ocr_used": False,
        "warnings": [],
    }

    path = Path(file_path) if file_path else None

    if not path:
        if strict_file_check:
            info["warnings"].append("documento nao baixado")
        return info

    if not path.exists():
        if strict_file_check:
            info["warnings"].append("arquivo baixado nao localizado no disco")
        return info

    try:
        extracted = extract_text_from_file(path, cfg=cfg)
    except Exception as err:
        info["warnings"].append(f"falha ao extrair texto: {err}")
        return info

    max_chars = _int_cfg(cfg, "max_text_chars", 60000)

    info.update({
        "ok": bool(extracted.get("ok")),
        "text": str(extracted.get("text") or "")[:max_chars],
        "file_type": str(extracted.get("file_type") or ""),
        "ocr_needed": bool(extracted.get("ocr_needed")),
        "ocr_used": bool(extracted.get("ocr_used")),
        "warnings": list(extracted.get("warnings") or []),
    })

    return info


def _base_decision(item, company_decision):
    company_decision = company_decision or {}
    status = str(company_decision.get("status") or item.get("empresa_filter_status") or "")
    action = "send_email"
    confidence = 0.78
    needs_review = False
    reason = "instrumento apto para comunicacao"
    evidence = []

    instrument_type = str(item.get("tipo_instrumento") or "")

    if status == "not_matched":
        action = "discard_company"
        confidence = 0.96 if company_decision.get("company_cnpjs") else 0.86
        reason = "empresa do acordo nao pertence a base do escritorio"
        evidence.append("filtro de empresas marcou o acordo como fora da base")
    elif status == "matched":
        action = "send_email"
        confidence = 0.95
        reason = "empresa do documento encontrada na base do escritorio"
        evidence.append("CNPJ do documento bateu com a base de clientes")
    elif status == "unknown":
        action = "send_email"
        confidence = 0.62
        needs_review = True
        reason = company_decision.get("reason") or "empresa do documento nao foi identificada com seguranca"
        evidence.append("caso mantido para alerta por prudencia, mas enviado para revisao")
    elif status in {"not_applicable", "disabled", ""}:
        action = "send_email"
        confidence = 0.84
        reason = "filtro de empresa nao se aplica ao tipo de instrumento"

        if instrument_type in {"convencao", "termoAditivoConvecao"}:
            evidence.append("convencao/termo de convencao normalmente abrange categoria")
    else:
        needs_review = True
        reason = f"status de filtro pouco conhecido: {status}"
        evidence.append("status de filtro nao reconhecido pela memoria operacional")

    cnpjs = company_decision.get("company_cnpjs") or item.get("empresas_documento_cnpjs") or []
    matches = company_decision.get("matched_companies") or item.get("empresas_escritorio_match") or []

    if cnpjs:
        evidence.append("CNPJ(s) de empresa no documento: " + ", ".join(cnpjs[:5]))

    if matches:
        names = [
            str(company.get("razao_social") or company.get("documento") or "").strip()
            for company in matches[:5]
        ]
        evidence.append("empresa(s) do escritorio encontrada(s): " + "; ".join([name for name in names if name]))

    return action, confidence, needs_review, reason, evidence


def similar_examples(db, item, terms, company_status="", limit=5, cfg=None, instrumento_id=None):
    cfg = cfg or {}
    threshold = _float_cfg(cfg, "similarity_threshold", 0.28)
    max_examples = _int_cfg(cfg, "similarity_pool", 500)
    rows = db.memory_examples(limit=max_examples, exclude_instrumento_id=instrumento_id)
    scored = []

    for row in rows:
        other_terms = _json(row.get("text_terms_json"), [])
        score = _term_similarity(terms, other_terms)

        if row.get("tipo_instrumento") and row.get("tipo_instrumento") == item.get("tipo_instrumento"):
            score += 0.08

        if row.get("sindicato_cnpj") and row.get("sindicato_cnpj") == item.get("sindicato_cnpj"):
            score += 0.08

        if company_status and row.get("empresa_filter_status") == company_status:
            score += 0.06

        score = min(score, 1.0)

        if score < threshold:
            continue

        scored.append({
            "instrumento_id": row.get("instrumento_id"),
            "score": round(score, 3),
            "decision": row.get("decision") or "",
            "final_action": row.get("final_action") or "",
            "confidence": row.get("confidence") or 0,
            "sindicato_nome": row.get("sindicato_nome") or "",
            "tipo_instrumento": row.get("tipo_instrumento") or "",
            "empresa_filter_status": row.get("empresa_filter_status") or "",
            "numero_registro": row.get("numero_registro") or "",
        })

    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:limit]


def evaluate_memory_decision(
    db,
    cfg,
    item,
    instrumento_id,
    file_path="",
    company_decision=None,
    summary="",
    run_id=None,
    alert_id=None,
    forced_action="",
    forced_reason="",
    strict_file_check=True,
    label_source="rules",
):
    action, confidence, needs_review, reason, evidence = _base_decision(item, company_decision)

    if forced_action:
        action = forced_action
        reason = forced_reason or reason
        confidence = max(confidence, 0.9)
        needs_review = action in REVIEW_ACTIONS
        label_source = label_source or "forced"

    file_info = _extract_file_signals(file_path, cfg, strict_file_check=strict_file_check)

    if file_info["warnings"]:
        evidence.extend(_short(warning, 140) for warning in file_info["warnings"][:3])

    if strict_file_check and action in SEND_ACTIONS:
        if not file_path:
            confidence = min(confidence, 0.58)
            needs_review = True
            evidence.append("decisao de envio sem arquivo baixado para conferencia")
        elif not file_info["ok"]:
            confidence = min(confidence, 0.6)
            needs_review = True
            evidence.append("texto do arquivo nao foi extraido com seguranca")

    if file_info["ocr_needed"]:
        confidence = min(confidence, 0.55)
        needs_review = True
        evidence.append("arquivo indicou necessidade de OCR")

    metadata_text = "\n".join([
        item.get("sindicato_nome") or "",
        item.get("tipo_descricao") or item.get("tipo_instrumento") or "",
        item.get("numero_registro") or "",
        item.get("numero_solicitacao") or "",
        item.get("empresa_filter_reason") or item.get("empresa_filter_motivo") or "",
        summary or item.get("resumo_mudancas") or "",
        file_info["text"],
    ])
    terms = text_terms(metadata_text, max_terms=_int_cfg(cfg, "max_terms", 90))
    company_status = str((company_decision or {}).get("status") or item.get("empresa_filter_status") or "")
    similar = similar_examples(
        db,
        item,
        terms,
        company_status=company_status,
        cfg=cfg,
        instrumento_id=instrumento_id,
    )

    if similar:
        current_family = _action_family(action)
        support = 0
        conflicts = 0

        for row in similar:
            family = _action_family(row.get("final_action") or row.get("decision"))
            comparable = (
                row.get("tipo_instrumento") == item.get("tipo_instrumento")
                or (
                    company_status
                    and row.get("empresa_filter_status") == company_status
                )
            )

            if family == current_family and comparable:
                support += 1
            elif family != "review" and comparable:
                conflicts += 1

        if conflicts:
            confidence = max(0.35, confidence - 0.2)
            needs_review = True
            evidence.append(f"{conflicts} caso(s) parecido(s) tiveram decisao diferente")
        elif support >= 2:
            confidence = min(0.99, confidence + 0.04)
            evidence.append(f"{support} caso(s) parecido(s) reforcaram a decisao")

    review_threshold = _float_cfg(cfg, "review_threshold", 0.7)

    if confidence < review_threshold:
        needs_review = True

    priority = 1

    if needs_review:
        priority = 2

    if confidence < 0.5 or any("diferente" in item for item in evidence):
        priority = 3

    decision = _decision_for_action(action, needs_review=needs_review)

    return {
        "instrumento_id": instrumento_id,
        "run_id": run_id,
        "alerta_id": alert_id,
        "decision": decision,
        "final_action": action,
        "confidence": round(float(confidence), 3),
        "reason": reason,
        "label_source": label_source,
        "evidence": [item for item in evidence if item],
        "signals": {
            "file_type": file_info.get("file_type") or "",
            "ocr_needed": file_info.get("ocr_needed") or False,
            "ocr_used": file_info.get("ocr_used") or False,
            "company_filter_status": company_status,
            "tipo_instrumento": item.get("tipo_instrumento") or "",
        },
        "similar": similar,
        "text_fingerprint": text_fingerprint(terms),
        "text_terms": terms,
        "file_path": file_path or item.get("arquivo_path") or "",
        "needs_review": bool(needs_review),
        "priority": priority,
    }


def record_memory_decision(db, cfg, item, instrumento_id, **kwargs):
    if not memory_enabled(cfg) or not instrumento_id:
        return {}

    record = evaluate_memory_decision(db, cfg, item, instrumento_id, **kwargs)
    decision_id = db.upsert_memory_decision(record)

    if record.get("needs_review"):
        review_id, created_review = db.upsert_review_item(record, decision_id=decision_id)
    else:
        db.clear_review_item_if_not_needed(instrumento_id)
        review_id, created_review = None, False

    record["decision_id"] = decision_id
    record["review_id"] = review_id
    record["review_created"] = created_review
    return record


def infer_historical_action(row):
    filter_status = str(row.get("empresa_filter_status") or "")

    if filter_status == "not_matched":
        return (
            "discard_company",
            row.get("empresa_filter_motivo") or "historico descartado por empresa fora da base",
            "historical_filter",
        )

    if row.get("alerta_id"):
        if int(row.get("alerta_enviado") or 0):
            return "email_sent", "historico de alerta enviado", "historical_email"

        return "email_pending", "historico de alerta criado e ainda pendente", "historical_email"

    if int(row.get("conhecido_antes_do_robo") or 0):
        return "discard_old", "instrumento conhecido antes do robo", "historical_baseline"

    return "review_unknown", "historico sem alerta e sem motivo claro de descarte", "historical_unknown"


def rebuild_operational_memory(
    db,
    cfg,
    limit=0,
    include_baseline=False,
    include_all=False,
    extract_files=False,
):
    rows = db.instruments_for_memory(
        limit=limit,
        include_baseline=include_baseline,
        actionable_only=not include_all,
    )
    indexed = 0
    review_created = 0
    skipped = 0

    for row in rows:
        instrumento_id = row.get("id")

        if not instrumento_id:
            skipped += 1
            continue

        action, reason, source = infer_historical_action(row)
        company_decision = {
            "status": row.get("empresa_filter_status") or "",
            "reason": row.get("empresa_filter_motivo") or "",
            "company_cnpjs": _json(row.get("empresas_documento_json"), []),
            "matched_companies": _json(row.get("empresas_escritorio_json"), []),
        }
        record = record_memory_decision(
            db,
            cfg,
            row,
            instrumento_id,
            file_path=(row.get("arquivo_path") or "") if extract_files else "",
            company_decision=company_decision,
            summary=row.get("resumo_mudancas") or "",
            alert_id=row.get("alerta_id"),
            forced_action=action,
            forced_reason=reason,
            strict_file_check=False,
            label_source=source,
        )

        if record:
            indexed += 1

            if record.get("review_created"):
                review_created += 1
        else:
            skipped += 1

    return {
        "total_rows": len(rows),
        "indexed": indexed,
        "review_created": review_created,
        "skipped": skipped,
    }


def _count(db, table, where="", params=()):
    if not db.table_exists(table):
        return 0

    sql = f"select count(*) as total from {table}"

    if where:
        sql += f" where {where}"

    row = db.con.execute(sql, params).fetchone()
    return int(row["total"] or 0) if row else 0


def _rows(db, sql, params=()):
    return [dict(row) for row in db.con.execute(sql, params).fetchall()]


def collect_memory_report(db, cfg, days=30):
    days = max(1, int(days or 30))
    since = _since(days)
    total = _count(db, "odysseus_memory")
    recent = _count(db, "odysseus_memory", "date(created_at) >= ?", (since,))
    pending = _count(db, "odysseus_review_queue", "coalesce(status, 'pending') = 'pending'")
    resolved = _count(db, "odysseus_review_queue", "coalesce(status, '') = 'resolved'")
    feedback = _count(db, "odysseus_memory_feedback")
    avg = 0.0

    if db.table_exists("odysseus_memory"):
        row = db.con.execute("""
            select avg(confidence) as media
            from odysseus_memory
            where date(created_at) >= ?
        """, (since,)).fetchone()
        avg = float(row["media"] or 0) if row else 0.0

    by_action = []
    by_review = []
    recent_reviews = []

    if db.table_exists("odysseus_memory"):
        by_action = _rows(
            db,
            """
            select coalesce(final_action, '(vazio)') as action, count(*) as total
            from odysseus_memory
            group by coalesce(final_action, '(vazio)')
            order by total desc, action
            """,
        )

    if db.table_exists("odysseus_review_queue"):
        by_review = _rows(
            db,
            """
            select coalesce(status, 'pending') as status, count(*) as total
            from odysseus_review_queue
            group by coalesce(status, 'pending')
            order by total desc, status
            """,
        )
        recent_reviews = db.review_items(status="pending", limit=10)

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y às %H:%M:%S"),
        "days": days,
        "since": since,
        "totals": {
            "memory": total,
            "recent": recent,
            "pending_reviews": pending,
            "resolved_reviews": resolved,
            "feedback": feedback,
            "avg_confidence": avg,
        },
        "by_action": by_action,
        "by_review": by_review,
        "recent_reviews": recent_reviews,
    }


def _pct(value):
    return f"{float(value or 0) * 100:.1f}%".replace(".", ",")


def format_memory_report(report):
    totals = report["totals"]
    lines = [
        "Relatório da Memória Operacional do Odysséus",
        f"Gerado em: {report['generated_at']}",
        f"Período recente: últimos {report['days']} dia(s), desde {report['since']}.",
        "",
        "Indicadores:",
        f"- Decisões registradas na memória: {totals['memory']}",
        f"- Decisões novas no período: {totals['recent']}",
        f"- Confiança média no período: {_pct(totals['avg_confidence'])}",
        f"- Casos pendentes na Central de Revisão: {totals['pending_reviews']}",
        f"- Casos resolvidos na Central de Revisão: {totals['resolved_reviews']}",
        f"- Feedbacks manuais registrados: {totals['feedback']}",
        "",
    ]

    if report["by_action"]:
        lines.append("Decisões por ação:")

        for row in report["by_action"]:
            lines.append(f"- {row.get('action')}: {row.get('total')}")

        lines.append("")

    if report["by_review"]:
        lines.append("Status da Central de Revisão:")

        for row in report["by_review"]:
            lines.append(f"- {row.get('status')}: {row.get('total')}")

        lines.append("")

    if report["recent_reviews"]:
        lines.append("Primeiros casos pendentes:")

        for row in report["recent_reviews"]:
            lines.append(
                f"- Revisão #{row.get('id')} | Instrumento #{row.get('instrumento_id')} | "
                f"{row.get('sindicato_nome') or '(sem sindicato)'} | "
                f"{row.get('numero_registro') or row.get('numero_solicitacao') or '(sem numero)'} | "
                f"confiança {_pct(row.get('confidence'))}"
            )

        lines.append("")

    if not report["recent_reviews"]:
        lines.append("Nenhum caso pendente encontrado na Central de Revisão.")

    return "\n".join(lines)


def format_review_items(items):
    if not items:
        return "Nenhum caso encontrado na Central de Revisão."

    lines = ["Central de Revisão do Odysséus", ""]

    for row in items:
        evidence = _json(row.get("evidence_json"), [])
        similar = _json(row.get("similar_json"), [])
        lines.extend([
            f"Revisão #{row.get('id')} | Instrumento #{row.get('instrumento_id')}",
            f"- Status: {row.get('status') or 'pending'} | Prioridade: {row.get('priority') or 0} | Confiança: {_pct(row.get('confidence'))}",
            f"- Decisão sugerida: {row.get('decision') or '-'} / {row.get('final_action') or '-'}",
            f"- Sindicato: {row.get('sindicato_nome') or '-'}",
            f"- Tipo: {row.get('tipo_instrumento') or '-'} | Registro: {row.get('numero_registro') or '-'} | Solicitação: {row.get('numero_solicitacao') or '-'}",
            f"- Vigência: {row.get('vigencia_inicio') or '-'} até {row.get('vigencia_fim') or '-'} | UF: {row.get('uf') or '-'}",
            f"- Motivo: {row.get('reason') or row.get('empresa_filter_motivo') or '-'}",
        ])

        if row.get("arquivo_path"):
            lines.append(f"- Arquivo: {row.get('arquivo_path')}")

        if evidence:
            lines.append("- Evidências:")

            for item in evidence[:5]:
                lines.append(f"  - {_short(item, 180)}")

        if similar:
            lines.append("- Casos parecidos:")

            for item in similar[:3]:
                label = item.get("numero_registro") or item.get("instrumento_id") or "-"
                lines.append(
                    f"  - {label}: {item.get('final_action') or item.get('decision') or '-'} "
                    f"com similaridade {_pct(item.get('score'))}"
                )

        lines.append("")

    return "\n".join(lines).rstrip()


def review_items_for_csv(items):
    rows = []

    for row in items:
        rows.append({
            "review_id": row.get("id"),
            "instrumento_id": row.get("instrumento_id"),
            "status": row.get("status"),
            "prioridade": row.get("priority"),
            "confianca": row.get("confidence"),
            "decisao": row.get("decision"),
            "acao": row.get("final_action"),
            "sindicato": row.get("sindicato_nome"),
            "tipo": row.get("tipo_instrumento"),
            "registro": row.get("numero_registro"),
            "solicitacao": row.get("numero_solicitacao"),
            "vigencia_inicio": row.get("vigencia_inicio"),
            "vigencia_fim": row.get("vigencia_fim"),
            "uf": row.get("uf"),
            "motivo": row.get("reason") or row.get("empresa_filter_motivo"),
            "arquivo": row.get("arquivo_path"),
        })

    return rows


def resolution_to_manual_label(resolution):
    value = str(resolution or "").strip().lower()

    if value in {"send", "enviar", "sent", "email", "correto_enviar"}:
        return "send", "manual_send", "revisao manual confirmou envio/comunicacao"

    if value in {"discard", "descartar", "ignored", "ignorar", "correto_descartar"}:
        return "discard", "manual_discard", "revisao manual confirmou descarte"

    if value in {"review", "revisar", "duvida", "unclear"}:
        return "review", "manual_review", "revisao manual manteve caso como duvidoso"

    return "review", "manual_review", f"resolucao manual registrada: {resolution}"
