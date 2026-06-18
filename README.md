# Odysséus CCT Monitor

Odysséus é uma automação em Python para consulta periódica de instrumentos coletivos registrados no Mediador/MTE.

O projeto mantém uma base histórica dos instrumentos já conhecidos, identifica novos registros e envia alertas por e-mail com resumo da execução.

## Objetivo

Automatizar uma rotina repetitiva de monitoramento, reduzindo o risco de perda de novos registros e organizando o histórico de instrumentos coletivos consultados.

O projeto foi desenvolvido para apoiar rotinas operacionais que dependem do acompanhamento de acordos coletivos, convenções coletivas e termos aditivos registrados no Mediador/MTE.

## Funcionalidades

* Consulta de instrumentos coletivos no Mediador/MTE.
* Monitoramento por CNPJ de sindicatos.
* Controle de base histórica local em SQLite.
* Identificação de novos instrumentos registrados.
* Filtro de segurança por ano de registro.
* Download de documentos vinculados aos instrumentos encontrados.
* Envio de alertas por e-mail.
* Execução diária com resumo da rotina.
* Geração de e-mail de conclusão mesmo quando não houver novos registros, se configurado.

## Tipos de instrumentos monitorados

* Acordo Coletivo.
* Convenção Coletiva.
* Termo Aditivo de Acordo Coletivo.
* Termo Aditivo de Convenção Coletiva.

## Estrutura do projeto

```text
app.py
odysseus/
  __init__.py
  cfg.py
  cli.py
  db.py
  emailer.py
  mte.py
  report.py
  util.py
config.example.toml
.env.example
requirements.txt
README.md
SECURITY.md
```

## Arquivo de configuração

O projeto utiliza um arquivo TOML para concentrar as principais configurações da rotina.

No repositório, é disponibilizado apenas o arquivo:

```text
config.example.toml
```

Esse arquivo serve como modelo seguro de configuração. Ele não contém dados reais de ambiente, credenciais, e-mails internos ou informações sensíveis.

Para executar o projeto localmente, copie o arquivo de exemplo:

```bash
cp config.example.toml config.toml
```

Depois edite o `config.toml` conforme o ambiente de uso.

A regra correta é:

```text
config.example.toml -> vai para o GitHub
config.toml         -> fica somente na máquina local
```

## Configuração de e-mail

O arquivo `config.toml` permite configurar remetente, destinatários e parâmetros de envio.

Exemplo:

```toml
[email]
enabled = true
provider = "smtp"
smtp_host = "smtp.gmail.com"
smtp_port = 587
use_tls = true

from_email = "email-do-robo@seudominio.com"
from_name = "Odysséus, Robô de Monitoramento de Convenções Coletivas"

to = ["destinatario@seudominio.com"]
cc = []
bcc = []

subject_prefix = "ColeConv"
send_when_empty = true
attach_new_files = true
dry_run = true
```

O campo `to` define quem receberá os alertas enviados pelo robô.

Também é possível configurar cópia e cópia oculta:

```toml
cc = ["copia@seudominio.com"]
bcc = ["copia-oculta@seudominio.com"]
```

O parâmetro `dry_run` controla se o e-mail será realmente enviado.

```toml
dry_run = true
```

Quando `dry_run` estiver como `true`, o sistema apenas gera um arquivo `.eml` local para conferência.

```toml
dry_run = false
```

Quando `dry_run` estiver como `false`, o sistema realiza o envio pelo SMTP configurado.

## Credenciais de e-mail

Por segurança, a senha do e-mail não deve ser salva no `config.toml`.

As credenciais SMTP devem ser informadas por variáveis de ambiente:

```bash
export ODYSSEUS_SMTP_USER="email-do-robo@seudominio.com"
export ODYSSEUS_SMTP_PASS="senha_de_app_sem_espacos"
```

Dessa forma, o arquivo de configuração pode ser usado localmente sem expor credenciais no repositório.

## Base de dados inicial

O projeto depende de uma base local em SQLite para controlar sindicatos monitorados, vínculos com empresas, instrumentos coletivos já conhecidos e alertas gerados pelo robô.

A base inicial pode ser estruturada a partir de uma planilha exportada de um sistema cadastral, como o Domínio, utilizada apenas como fonte para popular o banco local.

A planilha original não acompanha este repositório, pois pode conter dados internos, CNPJs, razões sociais, vínculos sindicais e informações operacionais sensíveis.

## Origem dos dados cadastrais

A planilha de origem deve conter, no mínimo, informações que permitam identificar os sindicatos a serem monitorados no Mediador/MTE.

Campos esperados na base de origem:

* identificação da empresa;
* razão social da empresa;
* CNPJ da empresa, quando aplicável;
* sindicato vinculado;
* nome do sindicato;
* CNPJ do sindicato;
* unidade federativa, quando disponível;
* categoria ou descrição do vínculo sindical, quando disponível.

A estrutura exata da planilha pode variar conforme a parametrização do sistema utilizado pela organização. Por isso, antes da importação, recomenda-se revisar os cabeçalhos e normalizar os campos essenciais.

## Banco SQLite local

O projeto utiliza SQLite como banco local de controle operacional.

A base local armazena informações como:

* sindicatos cadastrados;
* sindicatos selecionados para monitoramento;
* vínculos entre empresas e sindicatos;
* aliases ou nomes alternativos de sindicatos;
* instrumentos coletivos já conhecidos;
* instrumentos coletivos encontrados no Mediador/MTE;
* alertas de e-mail gerados e status de envio;
* inconsistências ou problemas identificados durante importações.

O arquivo de banco utilizado em produção deve ser configurado no `config.toml`:

```toml
[app]
db = "data/odysses_cct_base.sqlite"
```

O banco real de produção não deve ser versionado no GitHub.

## Fluxo técnico de preparação da base

O fluxo recomendado para preparar a base local é:

1. Exportar a planilha cadastral do sistema utilizado pela organização.
2. Revisar se os sindicatos possuem CNPJ.
3. Normalizar nomes, CNPJs e unidades federativas.
4. Importar os dados para o banco SQLite local.
5. Validar a quantidade de sindicatos candidatos ao monitoramento.
6. Executar a rotina de baseline.
7. Somente após o baseline executar a rotina diária.

## Normalização dos dados

Antes de gravar a base, recomenda-se aplicar os seguintes tratamentos:

* remover pontuação de CNPJs;
* descartar registros sem CNPJ de sindicato, quando o monitoramento depender de CNPJ;
* padronizar unidades federativas em letras maiúsculas;
* remover espaços duplicados em nomes;
* tratar sindicatos duplicados;
* criar aliases para sindicatos com nomes alternativos;
* registrar inconsistências em tabela própria ou log de importação.

Exemplo conceitual de normalização de CNPJ:

```text
Entrada: 00.000.000/0001-00
Saída:   00000000000100
```

## Baseline inicial

Antes de ativar o monitoramento diário, é necessário executar uma rotina de baseline.

O baseline consulta o Mediador/MTE para os sindicatos cadastrados e grava todos os instrumentos já existentes como conhecidos. Isso evita que documentos antigos sejam tratados como novidades na primeira execução do robô.

Fluxo do baseline:

1. Buscar sindicatos candidatos no banco local.
2. Consultar o Mediador/MTE por CNPJ do sindicato.
3. Consultar as unidades federativas configuradas no projeto.
4. Consultar os tipos de instrumento monitorados.
5. Gravar os instrumentos encontrados como já conhecidos.
6. Não gerar alerta de e-mail nessa etapa.

Comando:

```bash
python -u app.py seed-baseline
```

Após o baseline, recomenda-se validar se a rotina diária não identifica falsos positivos:

```bash
python -u app.py daily --no-send --no-download
```

O resultado esperado após a calibragem é:

```text
Novos instrumentos: 0
Erros: 0
```

## Rotina diária

Após a base estar calibrada, a rotina diária pode ser executada.

```bash
python -u app.py daily
```

A rotina diária realiza:

* consulta dos sindicatos monitorados;
* comparação com os instrumentos já gravados no banco;
* identificação de novos instrumentos;
* filtro por ano mínimo de registro;
* download de documentos, quando habilitado;
* criação de alertas;
* envio de e-mail com o resumo da execução.

Para testar sem enviar e-mail e sem baixar documentos:

```bash
python -u app.py daily --no-send --no-download
```

Para testar o e-mail em modo dry-run:

```bash
python -u app.py email-test
```

## Parâmetros relevantes

No `config.toml`, os principais parâmetros de monitoramento são:

```toml
[monitor]
ufs = ["DF", "GO"]
min_registration_year_to_alert = 2025
alert_without_registration_year = false
instrument_types = [
  "acordo",
  "convencao",
  "termoAditivoAcordo",
  "termoAditivoConvecao"
]
```

O parâmetro `min_registration_year_to_alert` evita que instrumentos antigos encontrados por mudança de configuração, paginação ou unidade federativa sejam tratados como novos alertas.

Exemplo:

```text
DF000145/2016 -> não gera alerta
DF000342/2026 -> pode gerar alerta
```

## Instalação

Instale as dependências:

```bash
pip install -r requirements.txt
```

Para Python anterior à versão 3.11, o projeto utiliza `tomli` para leitura de arquivos TOML.

## Segurança e privacidade

Este repositório foi preparado para não conter dados reais de produção.

Não devem ser versionados:

* senhas;
* arquivos `.env`;
* `config.toml` real;
* banco de dados real;
* logs de execução;
* e-mails gerados;
* documentos baixados;
* planilhas internas;
* arquivos de sessão ou HAR;
* qualquer arquivo contendo CNPJ, razão social, e-mail interno ou histórico operacional.

O repositório deve conter apenas:

* código-fonte;
* documentação;
* `config.example.toml`;
* `.env.example`;
* `.gitignore`;
* arquivos estáticos não sensíveis.

## Observação sobre ambiente real

Para executar o projeto em um ambiente real, cada usuário deve criar sua própria base local a partir dos dados da sua organização.

A estrutura do projeto permite o monitoramento, mas os dados de entrada devem permanecer fora do repositório por segurança e privacidade.
