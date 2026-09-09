from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from .cfg import sec
from .util import root


def _rows(db, sql, params=()):
    return [dict(row) for row in db.con.execute(sql, params).fetchall()]


def _one(db, sql, params=()):
    row = db.con.execute(sql, params).fetchone()
    return dict(row) if row else {}


def _count(db, table, where="", params=()):
    if not db.table_exists(table):
        return 0

    sql = f"select count(*) as total from {table}"

    if where:
        sql += f" where {where}"

    row = _one(db, sql, params)
    return int(row.get("total", 0) or 0)


def _since(days):
    days = max(1, int(days or 30))
    return (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _pct(part, total):
    if not total:
        return "0,0%"

    return f"{(part / total) * 100:.1f}%".replace(".", ",")


def _duration_label(seconds):
    seconds = int(seconds or 0)

    if seconds <= 0:
        return "-"

    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}min"

    if minutes:
        return f"{minutes}min {rest}s"

    return f"{rest}s"


def collect_health_report(db, cfg, days=30):
    days = max(1, int(days or 30))
    since = _since(days)

    total_instruments = _count(db, "instrumentos_mte")
    total_alerts = _count(db, "alertas_email")
    sent_alerts = _count(db, "alertas_email", "coalesce(enviado, 0) = 1")
    pending_alerts = _count(db, "alertas_email", "coalesce(enviado, 0) = 0")
    office_companies = _count(db, "empresas_escritorio")

    recent_new = _count(
        db,
        "instrumentos_mte",
        "coalesce(conhecido_antes_do_robo, 0) = 0 and date(created_at) >= ?",
        (since,),
    )
    recent_downloaded = _count(
        db,
        "instrumentos_mte",
        "coalesce(conhecido_antes_do_robo, 0) = 0 and date(created_at) >= ? "
        "and coalesce(arquivo_path, '') != ''",
        (since,),
    )
    recent_summaries = _count(
        db,
        "instrumentos_mte",
        "coalesce(conhecido_antes_do_robo, 0) = 0 and date(created_at) >= ? "
        "and coalesce(resumo_mudancas, '') != ''",
        (since,),
    )
    recent_filtered_company = _count(
        db,
        "instrumentos_mte",
        "coalesce(conhecido_antes_do_robo, 0) = 0 and date(created_at) >= ? "
        "and coalesce(empresa_filter_status, '') = 'not_matched'",
        (since,),
    )

    last_instrument = _one(
        db,
        """
        select created_at, numero_registro, tipo_instrumento, uf
        from instrumentos_mte
        where coalesce(conhecido_antes_do_robo, 0) = 0
        order by created_at desc, id desc
        limit 1
        """,
    )
    last_email = _one(
        db,
        """
        select enviado_em, assunto
        from alertas_email
        where coalesce(enviado, 0) = 1
        order by enviado_em desc, id desc
        limit 1
        """,
    )

    runs = []
    run_status = []
    query_status = []

    if db.table_exists("monitor_runs"):
        runs = _rows(
            db,
            """
            select
                id,
                started_at,
                finished_at,
                status,
                total_sindicatos,
                total_consultas,
                total_coletados,
                total_existentes,
                total_novos,
                total_baixados,
                total_erros,
                total_filtrados_empresa,
                total_alertas_criados,
                total_alertas_enviados,
                monitor_source,
                total_empresas,
                erro,
                case
                    when coalesce(finished_at, '') != ''
                    then strftime('%s', finished_at) - strftime('%s', started_at)
                    else null
                end as duracao_segundos
            from monitor_runs
            where date(started_at) >= ?
            order by started_at desc
            limit 20
            """,
            (since,),
        )
        run_status = _rows(
            db,
            """
            select coalesce(status, '(vazio)') as status, count(*) as total
            from monitor_runs
            where date(started_at) >= ?
            group by coalesce(status, '(vazio)')
            order by total desc, status
            """,
            (since,),
        )

    if db.table_exists("consultas_mte"):
        query_status = _rows(
            db,
            """
            select coalesce(status, '(vazio)') as status, count(*) as total
            from consultas_mte
            where date(created_at) >= ?
            group by coalesce(status, '(vazio)')
            order by total desc, status
            limit 12
            """,
            (since,),
        )

    daily_new = _rows(
        db,
        """
        select
            date(created_at) as dia,
            count(*) as total,
            sum(case when coalesce(arquivo_path, '') != '' then 1 else 0 end) as baixados,
            sum(case when coalesce(resumo_mudancas, '') != '' then 1 else 0 end) as resumos,
            sum(case when coalesce(empresa_filter_status, '') = 'not_matched' then 1 else 0 end) as filtrados_empresa
        from instrumentos_mte
        where coalesce(conhecido_antes_do_robo, 0) = 0
          and date(created_at) >= ?
        group by date(created_at)
        order by dia desc
        limit 14
        """,
        (since,),
    )
    filter_status = _rows(
        db,
        """
        select coalesce(empresa_filter_status, '(sem filtro)') as status, count(*) as total
        from instrumentos_mte
        where coalesce(conhecido_antes_do_robo, 0) = 0
          and date(created_at) >= ?
        group by coalesce(empresa_filter_status, '(sem filtro)')
        order by total desc, status
        """,
        (since,),
    )
    summary_status = _rows(
        db,
        """
        select
            coalesce(resumo_status, '(sem resumo)') as status,
            coalesce(arquivo_tipo_detectado, '(sem tipo)') as arquivo_tipo,
            count(*) as total
        from instrumentos_mte
        where coalesce(conhecido_antes_do_robo, 0) = 0
          and date(created_at) >= ?
        group by coalesce(resumo_status, '(sem resumo)'), coalesce(arquivo_tipo_detectado, '(sem tipo)')
        order by total desc, status
        limit 12
        """,
        (since,),
    )
    pending_by_error = _rows(
        db,
        """
        select coalesce(nullif(erro_envio, ''), '(sem erro registrado)') as erro, count(*) as total
        from alertas_email
        where coalesce(enviado, 0) = 0
        group by coalesce(nullif(erro_envio, ''), '(sem erro registrado)')
        order by total desc
        limit 8
        """,
    )

    points = []
    status = "saudável"

    if not runs:
        status = "em implantação"
        points.append(
            "A telemetria de execuções começa a ser preenchida a partir da próxima rotina diária."
        )

    if pending_alerts:
        status = "atenção" if status == "saudável" else status
        points.append(f"Existem {pending_alerts} alerta(s) pendente(s) no banco.")

    failed_runs = sum(
        int(row.get("total", 0) or 0)
        for row in run_status
        if str(row.get("status") or "").lower() in {"failed", "blocked", "error"}
    )

    if failed_runs:
        status = "atenção"
        points.append(f"{failed_runs} execução(ões) recente(s) terminaram com erro ou bloqueio.")

    blocked_queries = sum(
        int(row.get("total", 0) or 0)
        for row in query_status
        if "captcha" in str(row.get("status") or "").lower()
        or "blocked" in str(row.get("status") or "").lower()
    )

    if blocked_queries:
        status = "atenção"
        points.append(f"{blocked_queries} consulta(s) recente(s) sinalizaram captcha/bloqueio.")

    if recent_new and recent_summaries < recent_new - recent_filtered_company:
        points.append(
            "Nem todo instrumento novo comunicado possui resumo salvo; vale conferir downloads e extração de texto."
        )

    if not points:
        points.append("Nenhum ponto crítico foi identificado no período analisado.")

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y às %H:%M:%S"),
        "days": days,
        "since": since,
        "status": status,
        "app_name": sec(cfg, "app").get("name", "Odysséus"),
        "totals": {
            "instrumentos": total_instruments,
            "alertas": total_alerts,
            "alertas_enviados": sent_alerts,
            "alertas_pendentes": pending_alerts,
            "empresas_escritorio": office_companies,
            "novos_periodo": recent_new,
            "baixados_periodo": recent_downloaded,
            "resumos_periodo": recent_summaries,
            "filtrados_empresa_periodo": recent_filtered_company,
        },
        "last_instrument": last_instrument,
        "last_email": last_email,
        "runs": runs,
        "run_status": run_status,
        "query_status": query_status,
        "daily_new": daily_new,
        "filter_status": filter_status,
        "summary_status": summary_status,
        "pending_by_error": pending_by_error,
        "points": points,
    }


def format_health_text(report):
    totals = report["totals"]
    lines = [
        f"Health Report do {report['app_name']}",
        f"Gerado em: {report['generated_at']}",
        f"Período analisado: últimos {report['days']} dia(s), desde {report['since']}.",
        f"Status geral: {report['status'].upper()}",
        "",
        "Indicadores principais:",
        f"- Instrumentos no banco: {totals['instrumentos']}",
        f"- Novos instrumentos no período: {totals['novos_periodo']}",
        f"- Documentos baixados no período: {totals['baixados_periodo']}",
        f"- Resumos gerados no período: {totals['resumos_periodo']}",
        f"- Acordos filtrados por empresa fora da base: {totals['filtrados_empresa_periodo']}",
        f"- Alertas enviados: {totals['alertas_enviados']}",
        f"- Alertas pendentes: {totals['alertas_pendentes']}",
        f"- Empresas na base do escritório: {totals['empresas_escritorio']}",
        "",
    ]

    if report["runs"]:
        last = report["runs"][0]
        lines.extend([
            "Última execução registrada:",
            f"- Início: {last.get('started_at') or '-'}",
            f"- Fim: {last.get('finished_at') or '-'}",
            f"- Status: {last.get('status') or '-'}",
            f"- Duração: {_duration_label(last.get('duracao_segundos'))}",
            f"- Sindicatos: {last.get('total_sindicatos') or 0}",
            f"- Consultas: {last.get('total_consultas') or 0}",
            f"- Novos: {last.get('total_novos') or 0}",
            f"- Erros: {last.get('total_erros') or 0}",
            "",
        ])
    else:
        lines.extend([
            "Última execução registrada:",
            "- Ainda não há execução na tabela monitor_runs.",
            "",
        ])

    if report["daily_new"]:
        lines.append("Novidades por dia:")

        for row in report["daily_new"][:10]:
            lines.append(
                f"- {row.get('dia')}: {row.get('total') or 0} novo(s), "
                f"{row.get('baixados') or 0} baixado(s), "
                f"{row.get('filtrados_empresa') or 0} filtrado(s) por empresa."
            )

        lines.append("")

    if report["query_status"]:
        lines.append("Status das consultas no período:")

        for row in report["query_status"]:
            lines.append(f"- {row.get('status')}: {row.get('total')}")

        lines.append("")

    if report["pending_by_error"]:
        lines.append("Alertas pendentes por motivo:")

        for row in report["pending_by_error"]:
            lines.append(f"- {row.get('erro')}: {row.get('total')}")

        lines.append("")

    lines.append("Pontos de atenção:")

    for point in report["points"]:
        lines.append(f"- {point}")

    return "\n".join(lines)


def _status_color(status):
    status = str(status or "").lower()

    if status == "saudável":
        return "#146c43"

    if status == "atenção":
        return "#8a5a00"

    return "#444444"


def _metric_cell(label, value, hint=""):
    hint_html = f"<div class=\"metric-hint\">{escape(str(hint))}</div>" if hint else ""
    return (
        "<td class=\"metric\">"
        f"<div class=\"metric-label\">{escape(str(label))}</div>"
        f"<div class=\"metric-value\">{escape(str(value))}</div>"
        f"{hint_html}"
        "</td>"
    )


def _table(headers, rows):
    if not rows:
        return "<p class=\"muted\">Sem dados para exibir neste bloco.</p>"

    head = "".join(f"<th>{escape(str(header))}</th>" for header in headers)
    body = []

    for row in rows:
        body.append(
            "<tr>"
            + "".join(f"<td>{escape(str(value if value is not None else ''))}</td>" for value in row)
            + "</tr>"
        )

    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_health_html(report):
    totals = report["totals"]
    status = report["status"]
    color = _status_color(status)
    runs = []

    for row in report["runs"][:10]:
        runs.append([
            row.get("started_at") or "",
            row.get("status") or "",
            _duration_label(row.get("duracao_segundos")),
            row.get("total_sindicatos") or 0,
            row.get("total_consultas") or 0,
            row.get("total_novos") or 0,
            row.get("total_filtrados_empresa") or 0,
            row.get("total_erros") or 0,
        ])

    daily = [
        [
            row.get("dia") or "",
            row.get("total") or 0,
            row.get("baixados") or 0,
            row.get("resumos") or 0,
            row.get("filtrados_empresa") or 0,
        ]
        for row in report["daily_new"][:14]
    ]
    query_status = [[row.get("status") or "", row.get("total") or 0] for row in report["query_status"]]
    filter_status = [[row.get("status") or "", row.get("total") or 0] for row in report["filter_status"]]
    summary_status = [
        [row.get("status") or "", row.get("arquivo_tipo") or "", row.get("total") or 0]
        for row in report["summary_status"]
    ]
    pending = [[row.get("erro") or "", row.get("total") or 0] for row in report["pending_by_error"]]
    points = "".join(f"<li>{escape(point)}</li>" for point in report["points"])

    return f"""<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Health Report do {escape(report['app_name'])}</title>
    <style>
      body {{
        margin: 0;
        padding: 0;
        background: #f3f3f3;
        color: #151515;
        font-family: Arial, Helvetica, sans-serif;
      }}
      .wrap {{
        max-width: 1040px;
        margin: 0 auto;
        padding: 28px 18px 40px;
      }}
      .panel {{
        background: #ffffff;
        border: 1px solid #d9d9d9;
      }}
      .hero {{
        padding: 26px 30px;
        background: #111111;
        color: #ffffff;
      }}
      .hero h1 {{
        margin: 8px 0 8px;
        font-size: 30px;
        line-height: 1.15;
        letter-spacing: 0;
      }}
      .hero p {{
        margin: 0;
        color: #e8e8e8;
        line-height: 1.6;
      }}
      .eyebrow {{
        font-size: 12px;
        font-weight: bold;
        text-transform: uppercase;
        color: #cfcfcf;
      }}
      .status {{
        display: inline-block;
        margin-top: 16px;
        padding: 8px 12px;
        background: #ffffff;
        color: {color};
        border-radius: 4px;
        font-weight: bold;
      }}
      .content {{
        padding: 24px 30px 30px;
      }}
      .metrics {{
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        margin-bottom: 22px;
      }}
      .metric {{
        border: 1px solid #dddddd;
        padding: 14px;
        vertical-align: top;
      }}
      .metric-label {{
        font-size: 12px;
        text-transform: uppercase;
        color: #5a5a5a;
        font-weight: bold;
      }}
      .metric-value {{
        margin-top: 6px;
        font-size: 26px;
        line-height: 1.1;
        font-weight: 800;
        color: #111111;
      }}
      .metric-hint {{
        margin-top: 5px;
        font-size: 12px;
        color: #666666;
      }}
      h2 {{
        margin: 26px 0 10px;
        font-size: 18px;
        letter-spacing: 0;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        margin: 8px 0 16px;
      }}
      th, td {{
        border: 1px solid #dddddd;
        padding: 9px 10px;
        font-size: 13px;
        line-height: 1.4;
        text-align: left;
      }}
      th {{
        background: #f0f0f0;
        color: #222222;
      }}
      ul {{
        margin: 8px 0 0 20px;
        padding: 0;
      }}
      li {{
        margin: 6px 0;
        line-height: 1.5;
      }}
      .muted {{
        color: #666666;
      }}
      .footer {{
        padding: 16px 30px;
        background: #111111;
        color: #ffffff;
        font-size: 13px;
        line-height: 1.5;
      }}
      @media (max-width: 720px) {{
        .hero, .content, .footer {{
          padding-left: 18px;
          padding-right: 18px;
        }}
        .metrics, .metrics tbody, .metrics tr, .metric {{
          display: block;
          width: 100%;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="panel">
        <div class="hero">
          <div class="eyebrow">Health Report</div>
          <h1>Diagnóstico operacional do {escape(report['app_name'])}</h1>
          <p>Relatório gerado em {escape(report['generated_at'])}, analisando os últimos {report['days']} dia(s) desde {escape(report['since'])}.</p>
          <div class="status">Status geral: {escape(status.upper())}</div>
        </div>
        <div class="content">
          <table class="metrics" role="presentation">
            <tr>
              {_metric_cell("Novos no período", totals["novos_periodo"], "instrumentos gravados como novidades")}
              {_metric_cell("Baixados", totals["baixados_periodo"], "documentos salvos localmente")}
              {_metric_cell("Resumos", totals["resumos_periodo"], _pct(totals["resumos_periodo"], max(1, totals["novos_periodo"])))}
              {_metric_cell("Filtrados por empresa", totals["filtrados_empresa_periodo"], "acordos fora da base")}
            </tr>
            <tr>
              {_metric_cell("Alertas enviados", totals["alertas_enviados"], "histórico total")}
              {_metric_cell("Alertas pendentes", totals["alertas_pendentes"], "precisam de atenção se crescer")}
              {_metric_cell("Empresas na base", totals["empresas_escritorio"], "cadastro do escritório")}
              {_metric_cell("Instrumentos no banco", totals["instrumentos"], "histórico acumulado")}
            </tr>
          </table>

          <h2>Pontos de Atenção</h2>
          <ul>{points}</ul>

          <h2>Últimas Execuções</h2>
          {_table(["Início", "Status", "Duração", "Sindicatos", "Consultas", "Novos", "Filtrados", "Erros"], runs)}

          <h2>Novidades por Dia</h2>
          {_table(["Dia", "Novos", "Baixados", "Resumos", "Filtrados por empresa"], daily)}

          <h2>Status das Consultas</h2>
          {_table(["Status", "Total"], query_status)}

          <h2>Filtro de Empresas</h2>
          {_table(["Status", "Total"], filter_status)}

          <h2>Resumo Automático</h2>
          {_table(["Status", "Tipo de arquivo", "Total"], summary_status)}

          <h2>Alertas Pendentes</h2>
          {_table(["Motivo", "Total"], pending)}
        </div>
        <div class="footer">
          Odysséus, Robô de Monitoramento de Convenções Coletivas
        </div>
      </div>
    </div>
  </body>
</html>
"""


def write_health_html(cfg, report, output=""):
    base = Path(cfg["base"])

    if output:
        path = root(base, output)
    else:
        reports_dir = root(base, sec(cfg, "app").get("reports", "reports"))
        path = reports_dir / f"health_report_{datetime.now().strftime('%Y-%m-%d')}.html"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_health_html(report), encoding="utf-8")
    return path
