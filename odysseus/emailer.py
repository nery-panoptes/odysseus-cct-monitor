from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
import html
import mimetypes
import smtplib
import ssl

from .cfg import env, sec
from .util import now, root


class Emailer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ecfg = sec(cfg, "email")
        self.base = Path(cfg["base"])
        self.outbox = root(self.base, sec(cfg, "app").get("outbox", "outbox"))
        self.outbox.mkdir(parents=True, exist_ok=True)

    def banner_width(self):
        try:
            return int(self.ecfg.get("banner_width", 360))
        except Exception:
            return 360

    def html_body(self, body):
        safe = html.escape(body or "").replace("\n", "<br>\n")

        banner_html = ""
        banner = self.ecfg.get("banner_path", "")

        if banner:
            banner_path = root(self.base, banner)

            if banner_path.exists():
                width = self.banner_width()

                banner_html = f"""
                <div style="margin-top:24px;padding-top:12px;">
                    <img
                        src="cid:odysseus-banner"
                        alt="Odysséus - Robô de Monitoramento de Convenções Coletivas"
                        width="{width}"
                        style="display:block;width:{width}px;max-width:100%;height:auto;border:0;outline:none;text-decoration:none;"
                    >
                </div>
                """

        return f"""\
<html>
  <body style="margin:0;padding:0;background:#ffffff;">
    <div style="font-family:Arial,Helvetica,sans-serif;color:#222222;font-size:14px;line-height:1.55;">
      <div>
        {safe}
      </div>
      {banner_html}
    </div>
  </body>
</html>
"""

    def add_banner(self, msg):
        banner = self.ecfg.get("banner_path", "")

        if not banner:
            return

        path = root(self.base, banner)

        if not path.exists():
            return

        html_part = msg.get_payload()[-1]

        ctype, encoding = mimetypes.guess_type(path.name)

        if ctype is None or encoding is not None:
            ctype = "image/png"

        maintype, subtype = ctype.split("/", 1)

        html_part.add_related(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            cid="<odysseus-banner>",
            disposition="inline",
        )

    def subject(self, subject):
        prefix = str(self.ecfg.get("subject_prefix", "ColeConv") or "").strip()
        subject = str(subject or "").strip()

        if prefix and not subject.startswith(f"[{prefix}]"):
            return f"[{prefix}] {subject}"

        return subject

    def recipients(self):
        to = self.ecfg.get("to", []) or []
        cc = self.ecfg.get("cc", []) or []
        bcc = self.ecfg.get("bcc", []) or []

        recipients = []

        for item in to + cc + bcc:
            item = str(item or "").strip()

            if item:
                recipients.append(item)

        return to, cc, bcc, recipients

    def build_message(self, subject, body, attachments=None):
        attachments = attachments or []

        msg = EmailMessage()

        msg["Subject"] = self.subject(subject)

        sender = str(self.ecfg.get("from_email", "") or "").strip()
        name = str(self.ecfg.get("from_name", "") or "").strip()

        if name:
            msg["From"] = formataddr((name, sender))
        else:
            msg["From"] = sender

        to, cc, bcc, recipients = self.recipients()

        msg["To"] = ", ".join(to)

        if cc:
            msg["Cc"] = ", ".join(cc)

        msg.set_content(body or "")
        msg.add_alternative(self.html_body(body), subtype="html")
        self.add_banner(msg)

        for item in attachments:
            path = Path(item)

            if not path.exists():
                continue

            ctype, encoding = mimetypes.guess_type(path.name)

            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"

            maintype, subtype = ctype.split("/", 1)

            msg.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        return msg

    def send(self, subject, body, attachments=None):
        msg = self.build_message(subject, body, attachments)

        dry = bool(self.ecfg.get("dry_run", True))

        if dry:
            file = self.outbox / f"email-dry-run-{now().replace(':', '-')}.eml"
            file.write_bytes(bytes(msg))
            return {"sent": False, "dry_run": True, "path": str(file)}

        user = env("ODYSSEUS_SMTP_USER") or self.ecfg.get("from_email", "")
        password = env("ODYSSEUS_SMTP_PASS")

        if not password:
            raise RuntimeError("Defina ODYSSEUS_SMTP_PASS com a senha/app password do e-mail.")

        host = self.ecfg.get("smtp_host")
        port = int(self.ecfg.get("smtp_port", 587))

        to, cc, bcc, recipients = self.recipients()

        if not recipients:
            raise RuntimeError("Nenhum destinatário configurado em [email].to/cc/bcc.")

        context = ssl.create_default_context()

        with smtplib.SMTP(host, port, timeout=120) as smtp:
            if self.ecfg.get("use_tls", True):
                smtp.starttls(context=context)

            smtp.login(user, password)
            smtp.send_message(
                msg,
                from_addr=self.ecfg.get("from_email", ""),
                to_addrs=recipients,
            )

        return {"sent": True, "dry_run": False, "path": ""}