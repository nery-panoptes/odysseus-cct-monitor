#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from odysseus.cfg import loadcfg, sec
from odysseus.emailer import Emailer
from odysseus.summarizer import build_document_summary, detect_file_type


def first_downloaded_doc(base):
    downloads = base / sec(loadcfg(base / "config.toml"), "app").get("downloads", "downloads")

    for pattern in ("*.doc", "*.pdf", "*.docx", "*.rtf", "*.txt"):
        found = sorted(downloads.glob(pattern))

        if found:
            return found[0]

    raise RuntimeError(f"Nenhum arquivo de teste encontrado em {downloads}.")


def main():
    parser = argparse.ArgumentParser(
        description="Envia um e-mail de teste com resumo automático local do Odysséus."
    )
    parser.add_argument(
        "--to",
        required=True,
        help="E-mail pessoal que receberá o teste.",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config.toml"),
        help="Caminho do config.toml.",
    )
    parser.add_argument(
        "--file",
        default="",
        help="Arquivo específico para resumir/anexar. Se omitido, usa o primeiro arquivo da pasta downloads.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Envia de verdade. Sem esta opção, grava um .eml em outbox como dry-run.",
    )

    args = parser.parse_args()

    cfg = loadcfg(args.config)
    base = Path(cfg["base"])
    file_path = Path(args.file).expanduser() if args.file else first_downloaded_doc(base)

    if not file_path.is_absolute():
        file_path = base / file_path

    if not file_path.exists():
        raise RuntimeError(f"Arquivo não encontrado: {file_path}")

    detected = detect_file_type(file_path)
    summary = build_document_summary(file_path, cfg=cfg)

    email_cfg = cfg.setdefault("email", {})
    email_cfg["to"] = [args.to]
    email_cfg["cc"] = []
    email_cfg["bcc"] = []
    email_cfg["dry_run"] = not args.send

    body = [
        "Bom dia,",
        "",
        "Este é um teste controlado do resumo automático local do Odysséus.",
        "",
        f"Arquivo analisado: {file_path.name}",
        f"Tipo detectado: {detected.get('label')}",
        "",
        "Resumo automático das mudanças:",
    ]

    for line in (summary.get("summary") or "").splitlines():
        line = line.strip()

        if line:
            body.append(f"- {line}")

    body.extend([
        "",
        "O arquivo usado no teste segue anexo para conferência.",
        "",
        "Atenciosamente,",
        "Odysséus, Robô de Monitoramento de Convenções Coletivas",
    ])

    result = Emailer(cfg).send(
        "Teste do resumo automático local",
        "\n".join(body),
        attachments=[str(file_path)],
    )

    if result.get("dry_run"):
        print("Dry-run ativo. E-mail de teste gravado em:")
        print(result["path"])
        print("")
        print("Para enviar de verdade, rode novamente com --send.")
    else:
        print(f"E-mail de teste enviado para {args.to}.")


if __name__ == "__main__":
    main()
