# Atualização 2026-09-09 - Odysséus Memory

O Odysséus recebeu uma nova camada de inteligência operacional gratuita: **Odysséus Memory**.

Essa atualização melhora a conferência de documentos baixados, descartados e enviados por e-mail. O sistema passa a registrar decisões anteriores e usa esse histórico para comparar novos instrumentos com casos parecidos.

<p align="center">
  <img src="../../assets/odysseus-memory-review.png" alt="Odysséus com Memória Operacional" width="520">
</p>

## O que mudou

* Criação da Memória Operacional em SQLite.
* Registro de decisões com ação tomada, confiança, motivo e evidências.
* Comparação textual local com casos parecidos do histórico.
* Central de Revisão para documentos ambíguos.
* Feedback manual para confirmar envio, descarte ou necessidade de revisão.
* Validação dos destinatários de e-mail antes do envio.
* Indicadores da memória adicionados ao Health Report e ao resumo do GitHub Actions.

## Como funciona

Quando um documento novo é encontrado, o Odysséus coleta os sinais já disponíveis no próprio projeto:

* tipo de instrumento;
* sindicato;
* número de registro e solicitação;
* texto extraído do documento;
* resumo local;
* CNPJs encontrados;
* resultado do filtro de empresas;
* histórico de documentos semelhantes.

Com base nesses sinais, ele classifica a situação como envio, descarte ou revisão. Casos com baixa confiança, conflito de padrão, falha de leitura ou possível necessidade de OCR entram na Central de Revisão.

## Custo

A atualização mantém o objetivo de custo zero. Não há uso de OpenAI, Gemini, Claude ou qualquer API paga. A memória usa Python, SQLite e comparação textual local.

## Comandos

Reconstruir a memória com o histórico útil:

```bash
python -u app.py memory-index
```

Ver o diagnóstico da memória:

```bash
python -u app.py memory-report --days 30
```

Abrir a Central de Revisão:

```bash
python -u app.py review-center
```

Exportar a Central de Revisão:

```bash
python -u app.py review-center --export reports/review_center_$(date +"%Y-%m-%d").csv
```

Resolver um caso manualmente:

```bash
python -u app.py review-center --resolve 20 --decision send --note "Confirmado para envio."
```

Conferir os destinatários configurados:

```bash
python -u app.py email-config-check
```
