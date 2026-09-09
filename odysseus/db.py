import json
import sqlite3
from pathlib import Path

from .util import now


class Db:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.ensure_runtime_schema()

    def ensure_runtime_schema(self):
        cur = self.con.cursor()

        cur.execute("""
            create table if not exists monitor_runs (
                id integer primary key autoincrement,
                started_at text not null,
                finished_at text,
                status text,
                total_sindicatos integer default 0,
                total_consultas integer default 0,
                total_novos integer default 0,
                erro text
            )
        """)

        cur.execute("""
            create table if not exists consultas_mte (
                id integer primary key autoincrement,
                run_id integer,
                sindicato_cnpj text,
                sindicato_nome text,
                uf text,
                tipo_instrumento text,
                status text,
                http_code text,
                mensagem text,
                tempo_ms integer,
                created_at text default current_timestamp
            )
        """)

        cur.execute("""
            create table if not exists arquivos_instrumentos (
                id integer primary key autoincrement,
                instrumento_id integer,
                tipo_arquivo text,
                caminho text,
                nome_arquivo text,
                sha256 text,
                created_at text default current_timestamp
            )
        """)

        cur.execute("""
            create table if not exists empresas_escritorio (
                id integer primary key autoincrement,
                source_key text,
                empresa_id text,
                razao_social text,
                documento text,
                tipo_documento text,
                ativo integer default 1,
                origem text,
                updated_at text default current_timestamp,
                unique(documento, tipo_documento, origem)
            )
        """)

        self.ensure_column("instrumentos_mte", "arquivo_tipo_detectado", "text")
        self.ensure_column("instrumentos_mte", "resumo_mudancas", "text")
        self.ensure_column("instrumentos_mte", "resumo_status", "text")
        self.ensure_column("instrumentos_mte", "resumo_gerado_em", "text")
        self.ensure_column("instrumentos_mte", "ocr_necessario", "integer default 0")
        self.ensure_column("instrumentos_mte", "empresa_filter_status", "text")
        self.ensure_column("instrumentos_mte", "empresas_documento_json", "text")
        self.ensure_column("instrumentos_mte", "empresas_escritorio_json", "text")
        self.ensure_column("instrumentos_mte", "empresa_filter_motivo", "text")
        self.ensure_column("monitor_runs", "total_coletados", "integer default 0")
        self.ensure_column("monitor_runs", "total_existentes", "integer default 0")
        self.ensure_column("monitor_runs", "total_baixados", "integer default 0")
        self.ensure_column("monitor_runs", "total_erros", "integer default 0")
        self.ensure_column("monitor_runs", "total_filtrados_empresa", "integer default 0")
        self.ensure_column("monitor_runs", "total_ignorados_ano", "integer default 0")
        self.ensure_column("monitor_runs", "total_alertas_criados", "integer default 0")
        self.ensure_column("monitor_runs", "total_alertas_enviados", "integer default 0")
        self.ensure_column("monitor_runs", "monitor_source", "text")
        self.ensure_column("monitor_runs", "total_empresas", "integer default 0")
        self.ensure_column("monitor_runs", "total_memory_decisions", "integer default 0")
        self.ensure_column("monitor_runs", "total_review_items", "integer default 0")
        self.ensure_column("instrumentos_mte", "memory_decision", "text")
        self.ensure_column("instrumentos_mte", "memory_confidence", "real")
        self.ensure_column("instrumentos_mte", "memory_reason", "text")
        self.ensure_column("instrumentos_mte", "memory_review_status", "text")
        self.ensure_column("instrumentos_mte", "memory_updated_at", "text")

        cur.execute("""
            create table if not exists odysseus_memory (
                id integer primary key autoincrement,
                instrumento_id integer not null,
                run_id integer,
                alerta_id integer,
                decision text,
                final_action text,
                confidence real,
                reason text,
                label_source text,
                evidence_json text,
                signals_json text,
                similar_json text,
                text_fingerprint text,
                text_terms_json text,
                file_path text,
                needs_review integer default 0,
                created_at text default current_timestamp,
                updated_at text default current_timestamp,
                unique(instrumento_id)
            )
        """)

        cur.execute("""
            create table if not exists odysseus_review_queue (
                id integer primary key autoincrement,
                instrumento_id integer not null,
                decision_id integer,
                status text default 'pending',
                priority integer default 2,
                reason text,
                confidence real,
                created_at text default current_timestamp,
                updated_at text default current_timestamp,
                resolved_at text,
                resolution text,
                resolution_note text,
                unique(instrumento_id)
            )
        """)

        cur.execute("""
            create table if not exists odysseus_memory_feedback (
                id integer primary key autoincrement,
                instrumento_id integer not null,
                decision_id integer,
                review_id integer,
                feedback text,
                note text,
                created_at text default current_timestamp
            )
        """)

        cur.execute("""
            create index if not exists idx_odysseus_memory_action
            on odysseus_memory(final_action, decision)
        """)

        cur.execute("""
            create index if not exists idx_odysseus_review_status
            on odysseus_review_queue(status, priority, created_at)
        """)

        self.con.commit()

    def table_columns(self, table):
        return {
            row["name"]
            for row in self.con.execute(f"pragma table_info({table})").fetchall()
        }

    def table_exists(self, table):
        row = self.con.execute("""
            select 1
            from sqlite_master
            where type = 'table'
              and name = ?
            limit 1
        """, (table,)).fetchone()
        return row is not None

    def ensure_column(self, table, name, definition):
        if not self.table_exists(table):
            return

        if name in self.table_columns(table):
            return

        try:
            self.con.execute(f"alter table {table} add column {name} {definition}")
        except sqlite3.OperationalError as err:
            if "duplicate column name" not in str(err).lower():
                raise

    def counts(self):
        tables = [
            "sindicatos_cadastro",
            "sindicato_manual_atual",
            "sindicato_manual_2025",
            "empresas_sindicatos",
            "sindicatos_aliases",
            "instrumentos_conhecidos_manuais",
            "instrumentos_mte",
            "alertas_email",
            "import_issues",
        ]

        out = {}

        for table in tables:
            try:
                out[table] = self.con.execute(f"select count(*) from {table}").fetchone()[0]
            except sqlite3.Error:
                out[table] = None

        return out

    def monitor_candidates(self, only_with_cnpj=True):
        sql = """
            select
                c.id,
                c.codigo,
                c.nome,
                c.apelido,
                c.cnpj,
                c.uf_inferida,
                c.monitorar_sugerido,
                c.motivo_monitoramento,
                coalesce(a.ocorrencias_atual, 0) as ocorrencias_atual,
                coalesce(a.ocorrencias_2025, 0) as ocorrencias_2025,
                a.data_bases,
                a.status_encontrados
            from sindicatos_cadastro c
            left join sindicatos_aliases a
              on a.cadastro_cnpj_match = c.cnpj
              or a.cadastro_nome_match = c.nome
            where coalesce(c.monitorar_sugerido, 'sim') = 'sim'
        """

        if only_with_cnpj:
            sql += " and length(coalesce(c.cnpj, '')) >= 14 and c.cnpj != '00000000000000'"

        sql += " order by c.uf_inferida, c.nome"

        return [dict(row) for row in self.con.execute(sql).fetchall()]

    def replace_office_companies(self, rows, origem):
        origem = str(origem or "").strip() or "base_empresas"

        cur = self.con.cursor()
        cur.execute("delete from empresas_escritorio where origem = ?", (origem,))

        for row in rows or []:
            cur.execute("""
                insert or replace into empresas_escritorio (
                    source_key,
                    empresa_id,
                    razao_social,
                    documento,
                    tipo_documento,
                    ativo,
                    origem,
                    updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(row.get("source_key") or ""),
                str(row.get("empresa_id") or ""),
                str(row.get("razao_social") or ""),
                str(row.get("documento") or ""),
                str(row.get("tipo_documento") or ""),
                1 if row.get("ativo", 1) else 0,
                origem,
                now(),
            ))

        self.con.commit()

    def office_companies(self, document_type="cnpj", only_active=True):
        sql = """
            select *
            from empresas_escritorio
            where coalesce(documento, '') != ''
        """
        params = []

        if document_type:
            sql += " and tipo_documento = ?"
            params.append(document_type)

        if only_active:
            sql += " and coalesce(ativo, 1) = 1"

        sql += " order by razao_social, documento"

        return [dict(row) for row in self.con.execute(sql, params).fetchall()]

    def known_manual_summary(self):
        sql = """
            select
                sindicato_key,
                sindicato_nome,
                count(*) as ocorrencias,
                group_concat(distinct data_base) as data_bases,
                group_concat(distinct status) as status
            from (
                select sindicato_key, sindicato_nome, data_base, status from sindicato_manual_atual
                union all
                select sindicato_key, sindicato_nome, data_base, status from sindicato_manual_2025
            ) x
            where sindicato_key is not null and sindicato_key != ''
            group by sindicato_key, sindicato_nome
            order by ocorrencias desc, sindicato_nome
        """

        return [dict(row) for row in self.con.execute(sql).fetchall()]

    def pending_alerts(self):
        sql = """
            select
                a.*,
                i.sindicato_nome,
                i.tipo_instrumento,
                i.numero_registro,
                i.numero_solicitacao,
                i.data_registro,
                i.vigencia_inicio,
                i.vigencia_fim,
                i.uf,
                i.arquivo_path
            from alertas_email a
            left join instrumentos_mte i on i.id = a.instrumento_id
            where coalesce(a.enviado, 0) = 0
            order by a.id
        """

        return [dict(row) for row in self.con.execute(sql).fetchall()]

    def create_test_alert(self, subject="Alerta de teste do Odysséus"):
        cur = self.con.cursor()

        numero_registro = "TESTE-REGISTRO"
        numero_solicitacao = "TESTE-SOLICITACAO"
        tipo_instrumento = "convencao"

        cur.execute("""
            insert or ignore into instrumentos_mte (
                sindicato_cnpj,
                sindicato_nome,
                tipo_instrumento,
                numero_registro,
                numero_solicitacao,
                data_registro,
                vigencia_inicio,
                vigencia_fim,
                uf,
                origem_seed,
                conhecido_antes_do_robo,
                created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'teste', 0, ?)
        """, (
            "00000000000000",
            "SINDICATO DE TESTE DO ODYSSÉUS",
            tipo_instrumento,
            numero_registro,
            numero_solicitacao,
            now()[:10],
            now()[:10],
            "2027-12-31",
            "DF",
            now(),
        ))

        row = cur.execute("""
            select id
            from instrumentos_mte
            where numero_registro = ?
              and numero_solicitacao = ?
              and tipo_instrumento = ?
            limit 1
        """, (
            numero_registro,
            numero_solicitacao,
            tipo_instrumento,
        )).fetchone()

        if not row:
            raise RuntimeError("Não foi possível criar ou localizar o instrumento de teste.")

        instrumento_id = row["id"]

        existing_alert = cur.execute("""
            select id
            from alertas_email
            where instrumento_id = ?
              and coalesce(enviado, 0) = 0
              and assunto = ?
            limit 1
        """, (
            instrumento_id,
            subject,
        )).fetchone()

        if existing_alert:
            self.con.commit()
            return instrumento_id

        cur.execute("""
            insert into alertas_email (
                instrumento_id,
                assunto,
                destinatarios,
                enviado,
                anexos_json
            ) values (?, ?, ?, 0, ?)
        """, (
            instrumento_id,
            subject,
            json.dumps(["destinatario@seudominio.com.br"], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
        ))

        self.con.commit()
        return instrumento_id
    def save_mte_instrument(self, item, sindicato_nome="", known_before=True):
        cur = self.con.cursor()

        reg = item.get("numero_registro") or ""
        req = item.get("numero_solicitacao") or ""
        typ = item.get("tipo_instrumento") or ""

        if not reg and not req:
            return None, False

        old = cur.execute("""
            select id
            from instrumentos_mte
            where coalesce(numero_registro, '') = ?
              and coalesce(numero_solicitacao, '') = ?
              and coalesce(tipo_instrumento, '') = ?
            limit 1
        """, (reg, req, typ)).fetchone()

        was_new = old is None

        cur.execute("""
            insert or ignore into instrumentos_mte (
                sindicato_cnpj,
                sindicato_nome,
                tipo_instrumento,
                numero_registro,
                numero_solicitacao,
                data_registro,
                vigencia_inicio,
                vigencia_fim,
                uf,
                url_documento,
                origem_seed,
                conhecido_antes_do_robo
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("sindicato_cnpj") or "",
            sindicato_nome or item.get("sindicato_nome") or "",
            typ,
            reg,
            req,
            item.get("data_registro") or "",
            item.get("vigencia_inicio") or "",
            item.get("vigencia_fim") or "",
            item.get("uf") or "",
            item.get("url_documento") or "",
            "seed" if known_before else "bot",
            1 if known_before else 0,
        ))

        cur.execute("""
            update instrumentos_mte
            set
                sindicato_cnpj = coalesce(nullif(?, ''), sindicato_cnpj),
                sindicato_nome = coalesce(nullif(?, ''), sindicato_nome),
                data_registro = coalesce(nullif(?, ''), data_registro),
                vigencia_inicio = coalesce(nullif(?, ''), vigencia_inicio),
                vigencia_fim = coalesce(nullif(?, ''), vigencia_fim),
                uf = coalesce(nullif(?, ''), uf),
                url_documento = coalesce(nullif(?, ''), url_documento),
                conhecido_antes_do_robo = case
                    when ? = 1 then 1
                    else conhecido_antes_do_robo
                end
            where coalesce(numero_registro, '') = ?
              and coalesce(numero_solicitacao, '') = ?
              and coalesce(tipo_instrumento, '') = ?
        """, (
            item.get("sindicato_cnpj") or "",
            sindicato_nome or item.get("sindicato_nome") or "",
            item.get("data_registro") or "",
            item.get("vigencia_inicio") or "",
            item.get("vigencia_fim") or "",
            item.get("uf") or "",
            item.get("url_documento") or "",
            1 if known_before else 0,
            reg,
            req,
            typ,
        ))

        row = cur.execute("""
            select id
            from instrumentos_mte
            where coalesce(numero_registro, '') = ?
              and coalesce(numero_solicitacao, '') = ?
              and coalesce(tipo_instrumento, '') = ?
            limit 1
        """, (reg, req, typ)).fetchone()

        self.con.commit()

        if not row:
            return None, False

        return row["id"], was_new

    def set_instrument_file(self, instrumento_id, path):
        self.con.execute("""
            update instrumentos_mte
            set arquivo_path = ?
            where id = ?
        """, (str(path or ""), instrumento_id))
        self.con.commit()

    def set_instrument_summary(
        self,
        instrumento_id,
        summary,
        status="",
        file_type="",
        ocr_needed=False,
    ):
        self.con.execute("""
            update instrumentos_mte
            set
                resumo_mudancas = ?,
                resumo_status = ?,
                arquivo_tipo_detectado = ?,
                ocr_necessario = ?,
                resumo_gerado_em = ?
            where id = ?
        """, (
            str(summary or ""),
            str(status or ""),
            str(file_type or ""),
            1 if ocr_needed else 0,
            now(),
            instrumento_id,
        ))
        self.con.commit()

    def set_company_filter(
        self,
        instrumento_id,
        status,
        document_cnpjs=None,
        matched_companies=None,
        reason="",
    ):
        self.con.execute("""
            update instrumentos_mte
            set
                empresa_filter_status = ?,
                empresas_documento_json = ?,
                empresas_escritorio_json = ?,
                empresa_filter_motivo = ?
            where id = ?
        """, (
            str(status or ""),
            json.dumps(document_cnpjs or [], ensure_ascii=False),
            json.dumps(matched_companies or [], ensure_ascii=False),
            str(reason or ""),
            instrumento_id,
        ))
        self.con.commit()

    def previous_instrument(self, item, current_id=None):
        cnpj = item.get("sindicato_cnpj") or ""
        typ = item.get("tipo_instrumento") or ""
        current_vigencia = item.get("vigencia_inicio") or ""

        if not cnpj or not typ:
            return None

        params = [cnpj, typ]
        current_filter = ""

        if current_id:
            current_filter = "and id != ?"
            params.append(current_id)

        params.extend([current_vigencia, current_vigencia])

        sql = f"""
            select *
            from instrumentos_mte
            where sindicato_cnpj = ?
              and tipo_instrumento = ?
              and coalesce(arquivo_path, '') != ''
              {current_filter}
            order by
              case
                when ? != '' and coalesce(vigencia_inicio, '') < ? then 0
                else 1
              end,
              coalesce(vigencia_inicio, '') desc,
              coalesce(data_registro, '') desc,
              id desc
            limit 1
        """

        row = self.con.execute(sql, params).fetchone()
        return dict(row) if row else None

    def create_alert(self, instrumento_id, subject, recipients=None, attachments=None):
        recipients = recipients or []
        attachments = attachments or []

        old = self.con.execute("""
            select id
            from alertas_email
            where instrumento_id = ?
              and coalesce(enviado, 0) = 0
            limit 1
        """, (instrumento_id,)).fetchone()

        if old:
            return old["id"], False

        cur = self.con.cursor()

        cur.execute("""
            insert into alertas_email (
                instrumento_id,
                assunto,
                destinatarios,
                enviado,
                anexos_json
            ) values (?, ?, ?, 0, ?)
        """, (
            instrumento_id,
            subject,
            json.dumps(recipients, ensure_ascii=False),
            json.dumps(attachments, ensure_ascii=False),
        ))

        self.con.commit()
        return cur.lastrowid, True

    def mark_alerts_sent(self, alert_ids):
        if not alert_ids:
            return

        marks = ",".join(["?"] * len(alert_ids))

        self.con.execute(f"""
            update alertas_email
            set enviado = 1,
                enviado_em = datetime('now'),
                erro_envio = ''
            where id in ({marks})
        """, alert_ids)

        self.con.commit()

    def mark_alerts_error(self, alert_ids, error):
        if not alert_ids:
            return

        marks = ",".join(["?"] * len(alert_ids))

        self.con.execute(f"""
            update alertas_email
            set erro_envio = ?
            where id in ({marks})
        """, [str(error)] + alert_ids)

        self.con.commit()

    def begin_monitor_run(self):
        cur = self.con.cursor()
        cur.execute("""
            insert into monitor_runs (
                started_at,
                status
            ) values (?, 'running')
        """, (now(),))
        self.con.commit()
        return cur.lastrowid

    def update_monitor_run_context(
        self,
        run_id,
        total_sindicatos=0,
        monitor_source="",
        total_empresas=0,
    ):
        if not run_id:
            return

        self.con.execute("""
            update monitor_runs
            set
                total_sindicatos = ?,
                monitor_source = ?,
                total_empresas = ?
            where id = ?
        """, (
            int(total_sindicatos or 0),
            str(monitor_source or ""),
            int(total_empresas or 0),
            run_id,
        ))
        self.con.commit()

    def finish_monitor_run(self, run_id, status, stats=None, error=""):
        if not run_id:
            return

        stats = stats or {}

        self.con.execute("""
            update monitor_runs
            set
                finished_at = ?,
                status = ?,
                total_consultas = ?,
                total_novos = ?,
                total_coletados = ?,
                total_existentes = ?,
                total_baixados = ?,
                total_erros = ?,
                total_filtrados_empresa = ?,
                total_ignorados_ano = ?,
                total_alertas_criados = ?,
                total_alertas_enviados = ?,
                total_memory_decisions = ?,
                total_review_items = ?,
                erro = ?
            where id = ?
        """, (
            now(),
            str(status or ""),
            int(stats.get("total_queries", 0) or 0),
            int(stats.get("total_new", 0) or 0),
            int(stats.get("total_seen", 0) or 0),
            int(stats.get("total_existing", 0) or 0),
            int(stats.get("total_downloaded", 0) or 0),
            int(stats.get("total_errors", 0) or 0),
            int(stats.get("total_filtered_company", 0) or 0),
            int(stats.get("total_ignored_old", 0) or 0),
            int(stats.get("total_alerts_created", 0) or 0),
            int(stats.get("total_alerts_sent", 0) or 0),
            int(stats.get("total_memory_decisions", 0) or 0),
            int(stats.get("total_review_items", 0) or 0),
            str(error or ""),
            run_id,
        ))
        self.con.commit()

    def record_mte_query(
        self,
        run_id,
        sindicato_cnpj,
        sindicato_nome,
        uf,
        tipo_instrumento,
        status,
        http_code="",
        message="",
        elapsed_ms=0,
    ):
        if not run_id:
            return

        self.con.execute("""
            insert into consultas_mte (
                run_id,
                sindicato_cnpj,
                sindicato_nome,
                uf,
                tipo_instrumento,
                status,
                http_code,
                mensagem,
                tempo_ms,
                created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            str(sindicato_cnpj or ""),
            str(sindicato_nome or ""),
            str(uf or ""),
            str(tipo_instrumento or ""),
            str(status or ""),
            str(http_code or ""),
            str(message or ""),
            int(elapsed_ms or 0),
            now(),
        ))
        self.con.commit()

    def instrument_exists(self, item):
        reg = item.get("numero_registro") or ""
        req = item.get("numero_solicitacao") or ""
        typ = item.get("tipo_instrumento") or ""

        row = self.con.execute("""
            select id
            from instrumentos_mte
            where coalesce(numero_registro, '') = ?
              and coalesce(numero_solicitacao, '') = ?
              and coalesce(tipo_instrumento, '') = ?
            limit 1
        """, (reg, req, typ)).fetchone()

        return row["id"] if row else None

    def upsert_memory_decision(self, record):
        instrumento_id = int(record.get("instrumento_id") or 0)

        if not instrumento_id:
            return None

        now_value = now()
        evidence_json = json.dumps(record.get("evidence") or [], ensure_ascii=False)
        signals_json = json.dumps(record.get("signals") or {}, ensure_ascii=False)
        similar_json = json.dumps(record.get("similar") or [], ensure_ascii=False)
        terms_json = json.dumps(record.get("text_terms") or [], ensure_ascii=False)

        old = self.con.execute("""
            select id
            from odysseus_memory
            where instrumento_id = ?
            limit 1
        """, (instrumento_id,)).fetchone()

        values = (
            int(record.get("run_id") or 0) or None,
            int(record.get("alerta_id") or 0) or None,
            str(record.get("decision") or ""),
            str(record.get("final_action") or ""),
            float(record.get("confidence") or 0),
            str(record.get("reason") or ""),
            str(record.get("label_source") or ""),
            evidence_json,
            signals_json,
            similar_json,
            str(record.get("text_fingerprint") or ""),
            terms_json,
            str(record.get("file_path") or ""),
            1 if record.get("needs_review") else 0,
            now_value,
            instrumento_id,
        )

        if old:
            self.con.execute("""
                update odysseus_memory
                set
                    run_id = coalesce(?, run_id),
                    alerta_id = coalesce(?, alerta_id),
                    decision = ?,
                    final_action = ?,
                    confidence = ?,
                    reason = ?,
                    label_source = ?,
                    evidence_json = ?,
                    signals_json = ?,
                    similar_json = ?,
                    text_fingerprint = ?,
                    text_terms_json = ?,
                    file_path = ?,
                    needs_review = ?,
                    updated_at = ?
                where instrumento_id = ?
            """, values)
            decision_id = old["id"]
        else:
            cur = self.con.cursor()
            cur.execute("""
                insert into odysseus_memory (
                    run_id,
                    alerta_id,
                    decision,
                    final_action,
                    confidence,
                    reason,
                    label_source,
                    evidence_json,
                    signals_json,
                    similar_json,
                    text_fingerprint,
                    text_terms_json,
                    file_path,
                    needs_review,
                    updated_at,
                    instrumento_id
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            decision_id = cur.lastrowid

        review_status = "pending" if record.get("needs_review") else "not_required"

        if self.table_exists("instrumentos_mte"):
            self.con.execute("""
                update instrumentos_mte
                set
                    memory_decision = ?,
                    memory_confidence = ?,
                    memory_reason = ?,
                    memory_review_status = ?,
                    memory_updated_at = ?
                where id = ?
            """, (
                str(record.get("decision") or ""),
                float(record.get("confidence") or 0),
                str(record.get("reason") or ""),
                review_status,
                now_value,
                instrumento_id,
            ))

        self.con.commit()
        return decision_id

    def upsert_review_item(self, record, decision_id=None):
        if not record.get("needs_review"):
            return None, False

        instrumento_id = int(record.get("instrumento_id") or 0)

        if not instrumento_id:
            return None, False

        old = self.con.execute("""
            select id, status
            from odysseus_review_queue
            where instrumento_id = ?
            limit 1
        """, (instrumento_id,)).fetchone()

        now_value = now()
        priority = int(record.get("priority") or 2)

        if old:
            if str(old["status"] or "") == "resolved":
                return old["id"], False

            self.con.execute("""
                update odysseus_review_queue
                set
                    decision_id = coalesce(?, decision_id),
                    priority = ?,
                    reason = ?,
                    confidence = ?,
                    updated_at = ?
                where id = ?
            """, (
                int(decision_id or 0) or None,
                priority,
                str(record.get("reason") or ""),
                float(record.get("confidence") or 0),
                now_value,
                old["id"],
            ))
            self.con.commit()
            return old["id"], False

        cur = self.con.cursor()
        cur.execute("""
            insert into odysseus_review_queue (
                instrumento_id,
                decision_id,
                status,
                priority,
                reason,
                confidence,
                created_at,
                updated_at
            ) values (?, ?, 'pending', ?, ?, ?, ?, ?)
        """, (
            instrumento_id,
            int(decision_id or 0) or None,
            priority,
            str(record.get("reason") or ""),
            float(record.get("confidence") or 0),
            now_value,
            now_value,
        ))

        if self.table_exists("instrumentos_mte"):
            self.con.execute("""
                update instrumentos_mte
                set memory_review_status = 'pending',
                    memory_updated_at = ?
                where id = ?
            """, (now_value, instrumento_id))

        self.con.commit()
        return cur.lastrowid, True

    def clear_review_item_if_not_needed(self, instrumento_id, reason="auto_not_required"):
        instrumento_id = int(instrumento_id or 0)

        if not instrumento_id:
            return

        row = self.con.execute("""
            select id
            from odysseus_review_queue
            where instrumento_id = ?
              and coalesce(status, 'pending') = 'pending'
            limit 1
        """, (instrumento_id,)).fetchone()

        if not row:
            return

        now_value = now()

        self.con.execute("""
            update odysseus_review_queue
            set
                status = 'resolved',
                resolved_at = ?,
                resolution = ?,
                resolution_note = 'Reavaliação automática não exige mais revisão.',
                updated_at = ?
            where id = ?
        """, (
            now_value,
            str(reason or "auto_not_required"),
            now_value,
            row["id"],
        ))

        if self.table_exists("instrumentos_mte"):
            self.con.execute("""
                update instrumentos_mte
                set memory_review_status = 'not_required',
                    memory_updated_at = ?
                where id = ?
            """, (now_value, instrumento_id))

        self.con.commit()

    def memory_examples(self, limit=500, exclude_instrumento_id=None):
        if not self.table_exists("odysseus_memory"):
            return []

        params = []
        where = "where coalesce(m.final_action, '') != ''"

        if exclude_instrumento_id:
            where += " and m.instrumento_id != ?"
            params.append(int(exclude_instrumento_id))

        params.append(int(limit or 500))

        sql = f"""
            select
                m.*,
                i.sindicato_cnpj,
                i.sindicato_nome,
                i.tipo_instrumento,
                i.numero_registro,
                i.numero_solicitacao,
                i.empresa_filter_status
            from odysseus_memory m
            left join instrumentos_mte i on i.id = m.instrumento_id
            {where}
            order by m.updated_at desc, m.id desc
            limit ?
        """

        return [dict(row) for row in self.con.execute(sql, params).fetchall()]

    def instruments_for_memory(self, limit=0, include_baseline=False, actionable_only=True):
        if not self.table_exists("instrumentos_mte"):
            return []

        params = []
        where = "where 1 = 1"

        if not include_baseline:
            where += " and coalesce(i.conhecido_antes_do_robo, 0) = 0"

        if actionable_only:
            where += """
                and (
                    a.id is not null
                    or coalesce(i.empresa_filter_status, '') != ''
                    or coalesce(i.arquivo_path, '') != ''
                    or coalesce(i.resumo_mudancas, '') != ''
                )
            """

        sql = f"""
            select
                i.*,
                a.id as alerta_id,
                a.enviado as alerta_enviado,
                a.enviado_em as alerta_enviado_em,
                a.erro_envio as alerta_erro_envio
            from instrumentos_mte i
            left join alertas_email a on a.instrumento_id = i.id
            {where}
            order by i.id desc
        """

        if limit:
            sql += " limit ?"
            params.append(int(limit))

        return [dict(row) for row in self.con.execute(sql, params).fetchall()]

    def review_items(self, status="pending", limit=30):
        if not self.table_exists("odysseus_review_queue"):
            return []

        status = str(status or "pending").strip().lower()
        params = []
        where = ""

        if status != "all":
            where = "where coalesce(q.status, 'pending') = ?"
            params.append(status)

        params.append(int(limit or 30))

        sql = f"""
            select
                q.*,
                m.decision,
                m.final_action,
                m.evidence_json,
                m.similar_json,
                i.sindicato_nome,
                i.sindicato_cnpj,
                i.tipo_instrumento,
                i.numero_registro,
                i.numero_solicitacao,
                i.data_registro,
                i.vigencia_inicio,
                i.vigencia_fim,
                i.uf,
                i.arquivo_path,
                i.empresa_filter_status,
                i.empresa_filter_motivo
            from odysseus_review_queue q
            left join odysseus_memory m on m.id = q.decision_id
            left join instrumentos_mte i on i.id = q.instrumento_id
            {where}
            order by
                case coalesce(q.status, 'pending') when 'pending' then 0 else 1 end,
                q.priority desc,
                q.created_at desc
            limit ?
        """

        return [dict(row) for row in self.con.execute(sql, params).fetchall()]

    def resolve_review_item(self, review_id=0, instrumento_id=0, resolution="", note=""):
        params = []
        where = ""

        if review_id:
            where = "id = ?"
            params.append(int(review_id))
        elif instrumento_id:
            where = "instrumento_id = ?"
            params.append(int(instrumento_id))
        else:
            raise RuntimeError("Informe review_id ou instrumento_id para resolver a revisão.")

        row = self.con.execute(
            f"select * from odysseus_review_queue where {where} limit 1",
            params,
        ).fetchone()

        if not row:
            raise RuntimeError("Item de revisão não encontrado.")

        now_value = now()

        self.con.execute(f"""
            update odysseus_review_queue
            set
                status = 'resolved',
                resolved_at = ?,
                resolution = ?,
                resolution_note = ?,
                updated_at = ?
            where {where}
        """, [now_value, str(resolution or ""), str(note or ""), now_value] + params)

        self.con.execute("""
            insert into odysseus_memory_feedback (
                instrumento_id,
                decision_id,
                review_id,
                feedback,
                note,
                created_at
            ) values (?, ?, ?, ?, ?, ?)
        """, (
            int(row["instrumento_id"] or 0),
            int(row["decision_id"] or 0) or None,
            int(row["id"] or 0),
            str(resolution or ""),
            str(note or ""),
            now_value,
        ))

        if self.table_exists("instrumentos_mte"):
            self.con.execute("""
                update instrumentos_mte
                set memory_review_status = 'resolved',
                    memory_updated_at = ?
                where id = ?
            """, (now_value, int(row["instrumento_id"] or 0)))

        self.con.commit()
        return dict(row)

    def update_memory_manual_label(self, instrumento_id, decision, final_action, reason):
        now_value = now()
        self.con.execute("""
            update odysseus_memory
            set
                decision = ?,
                final_action = ?,
                confidence = 1.0,
                reason = ?,
                label_source = 'manual',
                needs_review = 0,
                updated_at = ?
            where instrumento_id = ?
        """, (
            str(decision or ""),
            str(final_action or ""),
            str(reason or ""),
            now_value,
            int(instrumento_id or 0),
        ))

        if self.table_exists("instrumentos_mte"):
            self.con.execute("""
                update instrumentos_mte
                set
                    memory_decision = ?,
                    memory_confidence = 1.0,
                    memory_reason = ?,
                    memory_review_status = 'resolved',
                    memory_updated_at = ?
                where id = ?
            """, (
                str(decision or ""),
                str(reason or ""),
                now_value,
                int(instrumento_id or 0),
            ))

        self.con.commit()

    def close(self):
        self.con.close()
