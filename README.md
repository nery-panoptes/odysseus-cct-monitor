# Odysséus CCT Monitor

Odysséus é uma automação em Python para consulta periódica de instrumentos coletivos registrados no Mediador/MTE.

O projeto mantém uma base histórica dos instrumentos já conhecidos, identifica novos registros e envia alertas por e-mail com resumo da execução.

## Objetivo

Automatizar uma rotina repetitiva de monitoramento, reduzindo o risco de perda de novos registros e organizando o histórico de instrumentos coletivos consultados.

## Funcionalidades

- Consulta de instrumentos coletivos no Mediador/MTE.
- Monitoramento por CNPJ de sindicatos.
- Controle de base histórica local em SQLite.
- Identificação de novos instrumentos registrados.
- Filtro de segurança por ano de registro.
- Envio de alertas por e-mail.
- Execução diária com resumo da rotina.

## Tipos de instrumentos monitorados

- Acordo Coletivo.
- Convenção Coletiva.
- Termo Aditivo de Acordo Coletivo.
- Termo Aditivo de Convenção Coletiva.

## Estrutura do projeto

app.py
odysseus/
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

## Configuração

Copie o arquivo de exemplo:

cp config.example.toml config.toml

Configure o arquivo config.toml com os dados do ambiente local.

As credenciais SMTP devem ser definidas por variáveis de ambiente:

export ODYSSEUS_SMTP_USER="seu-email@seudominio.com"
export ODYSSEUS_SMTP_PASS="sua_senha_de_app_sem_espacos"

## Execução

Instale as dependências:

pip install -r requirements.txt

Rodar a rotina diária:

python -u app.py daily

Rodar sem enviar e-mail e sem baixar anexos:

python -u app.py daily --no-send --no-download

Rodar teste de e-mail em modo dry-run:

python -u app.py email-test

## Segurança e privacidade

Este repositório foi preparado para não conter dados reais de produção.

Não devem ser versionados:

- senhas;
- arquivos .env;
- config.toml real;
- banco de dados real;
- logs de execução;
- e-mails gerados;
- documentos baixados;
- planilhas internas;
- arquivos de sessão ou HAR.

O banco incluído neste repositório é apenas uma cópia sanitizada, sem dados reais.
