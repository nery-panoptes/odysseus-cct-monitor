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

        self.con.commit()

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
            json.dumps(["jose.nery@felipegaiao.com.br"], ensure_ascii=False),
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

    def close(self):
        self.con.close()