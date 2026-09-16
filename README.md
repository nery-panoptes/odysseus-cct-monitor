<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/nery-panoptes/odysseus-cct-monitor@main/assets/odysseus-github-banner.png" alt="Odysséus CCT Monitor" width="100%">
</p>

# Odysséus CCT Monitor

Odysséus é uma automação em Python para consultar periodicamente instrumentos coletivos registrados no Mediador/MTE, manter histórico local e enviar alertas por e-mail quando surgirem novas convenções, acordos ou termos aditivos relevantes.

## Atualização 2026-07-24

Esta versão adiciona uma camada de inteligência operacional ao monitoramento:

* filtro de sindicatos por planilha online do Google Sheets;
* filtro de acordos específicos por CNPJ das empresas/clientes do escritório;
* resumo automático local dos documentos baixados;
* detecção do tipo de arquivo baixado e indicação de necessidade de OCR;
* melhoria no tratamento de bloqueios/captcha do Mediador/MTE;
* configuração preparada para execução local ou agendada por GitHub Actions.

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/nery-panoptes/odysseus-cct-monitor@main/assets/odysseus-lets-go-larp.png" alt="Meme de atualização do Odysséus: lets go larp" width="520">
</p>

## Atualização 2026-09-09

O Odysséus recebeu o **Health Report**, um diagnóstico operacional gratuito para acompanhar a saúde das rotinas.

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/nery-panoptes/odysseus-cct-monitor@main/assets/odysseus-health-report.png" alt="Odysséus sentindo o impacto de uma rotina sem diagnóstico" width="420">
</p>

* registro estruturado de cada execução diária;
* rastreamento de cada consulta ao Mediador/MTE;
* status, tempo de resposta, HTTP e mensagem por consulta;
* relatório em terminal e HTML;
* resumo automático no GitHub Actions;
* leitura direta do SQLite, sem API paga e sem consumo de tokens.

Comandos:

```bash
python -u app.py health-report --days 30
python -u app.py diagnose --days 30 --html
```

## Atualização 2026-09-09 - Odysséus Memory

A nova camada **Odysséus Memory** adiciona uma memória operacional gratuita ao monitoramento.

Ela registra decisões anteriores, compara documentos novos com casos parecidos e separa situações ambíguas em uma Central de Revisão. A ideia é tornar o robô mais criterioso sem depender de API externa, cobrança por uso ou limite diário de tokens.

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/nery-panoptes/odysseus-cct-monitor@main/assets/odysseus-memory-review.png" alt="Odysséus com Memória Operacional" width="520">
</p>

Principais pontos:

* histórico local de documentos enviados, descartados, baixados e resumidos;
* decisão sugerida com grau de confiança;
* evidências da triagem, como filtro de empresas, CNPJs e necessidade de OCR;
* comparação textual com casos parecidos;
* Central de Revisão para feedback manual;
* conferência de destinatários de e-mail antes do envio.

Página da atualização: [docs/updates/2026-09-09-memory-review.md](docs/updates/2026-09-09-memory-review.md)

## Atualização 2026-09-16 - Robustez e confiabilidade

Esta rodada não adiciona funcionalidade nova — melhora a forma como o Odysséus reage quando o Mediador/MTE, a planilha online ou o SMTP falham no meio do caminho.

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/nery-panoptes/odysseus-cct-monitor@main/assets/odysseus-update-reading.png" alt="Odysséus estudando sua própria autodefesa" width="420">
</p>

* nova tentativa automática, com espera progressiva, em toda chamada de rede (MTE, planilha online e SMTP);
* distinção entre falha passageira (tenta de novo) e falha permanente, como senha errada (reporta na hora, sem insistir à toa);
* paginação do Mediador/MTE tolera algumas páginas vazias ou repetidas antes de encerrar a busca, em vez de parar na primeira;
* detecção explícita quando o layout do Mediador/MTE muda de um jeito que o parser não reconhece mais, em vez de virar silenciosamente "nenhum resultado";
* extração de texto de documentos `.doc` antigos agora funciona também no runner Linux do GitHub Actions, não só no macOS;
* logs de cada execução diária passam a ficar arquivados por 90 dias como artefato do workflow.

## O que ele faz

* Consulta acordos coletivos, convenções coletivas e termos aditivos no Mediador/MTE.
* Controla uma base histórica em SQLite para evitar alertas duplicados.
* Baixa os documentos encontrados e mantém os anexos no e-mail.
* Envia e-mail de conclusão mesmo quando nada novo é encontrado, se configurado.
* Resume localmente os pontos principais dos documentos novos.
* Filtra acordos específicos de empresa quando a empresa do documento não está na base de clientes.
* Gera diagnóstico operacional com o Health Report.
* Registra decisões na Memória Operacional e encaminha casos ambíguos para revisão.

## IA local gratuita

A IA desta versão se chama, dentro do projeto, **Odysséus Local Rules**.

Ela não usa OpenAI, Gemini, Claude ou qualquer API paga. O resumo é feito no próprio Python, com extração de texto, identificação de cláusulas, datas, valores, benefícios, jornada, descontos e comparação com documento anterior quando existir arquivo comparável salvo.

Isso mantém custo zero e evita limite diário de tokens. Para PDFs escaneados, o projeto pode indicar necessidade de OCR. O OCR também pode ser mantido em custo zero usando ferramentas locais como Tesseract/OCRmyPDF, caso sejam instaladas no ambiente.

## Fluxo atual

1. O Odysséus carrega o `config.toml` local.
2. Ele abre o banco SQLite configurado em `[app].db`.
3. Ele carrega os sindicatos candidatos.
4. Se `[monitor_source].source = "google_sheet_filter"`, a lista bruta do banco é filtrada pelos nomes presentes na coluna configurada da planilha online.
5. Para cada sindicato, UF e tipo de instrumento, ele consulta o Mediador/MTE.
6. Se o MTE retornar bloqueio, captcha ou Cloudflare, a rotina registra o erro e pode abortar depois do limite configurado.
7. Quando encontra instrumento novo, ele grava no banco e baixa o documento.
8. O resumo local extrai texto do arquivo e gera bullets curtos para o e-mail.
9. Se o instrumento for acordo ou termo aditivo de acordo, o sistema procura CNPJs de empresas nas partes do documento.
10. Se a empresa do acordo não estiver na base de empresas/clientes, o alerta é filtrado e não é enviado.
11. Se passar nos filtros, o e-mail é enviado com o resumo e com o documento anexo.

## Arquivos seguros e arquivos privados

Este repositório público deve receber apenas código, documentação e exemplos.

Ficam fora do GitHub:

* `config.toml` real;
* `.env` real;
* banco SQLite de produção;
* planilhas internas;
* relatórios gerados;
* logs;
* downloads do Mediador/MTE;
* arquivos `.eml` gerados em dry-run.

O arquivo público correto é:

```text
config.example.toml
```

Para executar em um ambiente real, copie:

```bash
cp config.example.toml config.toml
```

Depois edite o `config.toml` localmente.

## Instalação local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

No macOS, também existe um script auxiliar:

```bash
bash scripts/setup_mac.sh
```

## Configuração de e-mail

A senha SMTP não deve ficar no `config.toml`.

Use variáveis de ambiente:

```bash
export ODYSSEUS_SMTP_USER="email-do-robo@seudominio.com"
export ODYSSEUS_SMTP_PASS="senha_de_app"
```

No GitHub Actions, esses valores devem ser cadastrados como **Repository secrets**.

## Fonte dos sindicatos

O modo padrão usa o banco SQLite local:

```toml
[monitor_source]
source = "database"
```

Para filtrar a base local usando uma planilha compartilhada:

```toml
[monitor_source]
source = "google_sheet_filter"
google_sheet_url = "https://docs.google.com/spreadsheets/d/SEU_ID/edit?gid=SUA_ABA"
filter_column = "NOME DO SINDICATO"
respect_monitor_ufs = true
```

Nesse modo, a planilha online funciona como régua operacional: o banco pode ter mais sindicatos, mas a rotina diária consulta apenas os nomes que continuam ativos na planilha.

## Base de empresas

Para filtrar acordos específicos de empresas, informe uma planilha privada com CNPJ das empresas/clientes:

```toml
[office_companies]
enabled = true
source_file = "data/empresas.example.xlsx"
sheet_name = ""
refresh_on_daily = true
company_specific_types = ["acordo", "termoAditivoAcordo"]
alert_when_company_unknown = true
```

A planilha precisa ter uma coluna de CNPJ ou CPF/CNPJ e, preferencialmente, uma coluna de razão social. Ela não deve ser enviada para o GitHub.

## Comandos principais

Conferir a base de empresas:

```bash
python -u app.py office-companies-check
```

Conferir o filtro da planilha online:

```bash
python -u app.py monitor-source-check
```

Rodar sem enviar e sem baixar arquivos:

```bash
python -u app.py daily --no-send --no-download
```

Rodar a rotina diária real:

```bash
python -u app.py daily
```

Gerar diagnóstico operacional:

```bash
python -u app.py health-report --days 30 --html
```

Reconstruir a memória operacional:

```bash
python -u app.py memory-index
```

Ver a Central de Revisão:

```bash
python -u app.py review-center
```

Conferir configuração de e-mail:

```bash
python -u app.py email-config-check
```

Gerar baseline inicial:

```bash
python -u app.py seed-baseline
```

Enviar um e-mail de teste com resumo local:

```bash
python -u scripts/test_summary_email.py --to seu-email-pessoal@seudominio.com --send
```

## GitHub Actions

Há um exemplo de workflow em:

```text
docs/github-actions-odysseus-daily.example.yml
```

Ele deve ser copiado para `.github/workflows/odysseus-daily.yml` apenas no repositório que vai executar a rotina de verdade.

Para produção, mantenha o `config.toml` real como Secret ou gere o arquivo durante o workflow. Nunca publique banco, planilhas internas ou senha SMTP.

## Segurança

O Odysséus não burla captcha nem mecanismos de proteção do Mediador/MTE. Quando o site exige captcha, Cloudflare ou bloqueio de sessão, o sistema registra o erro e interrompe de forma controlada conforme a configuração.

Se uma senha ou token for publicado acidentalmente, revogue o segredo imediatamente e gere outro.
