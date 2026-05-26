# Webhooks - Billing Core

## Webhook recebido do Asaas

Endpoint atual:

- `POST /v1/webhooks/asaas`

## Validacoes aplicadas

- header `asaas-access-token` obrigatorio
- comparacao segura com `ASAAS_WEBHOOK_SECRET`
- corpo obrigatorio
- protecao contra replay baseada em hash do corpo na janela `WEBHOOK_REPLAY_TTL_SECONDS`
- rate limiting

## Processamento interno

1. O payload cru e convertido em JSON.
2. O adapter do gateway normaliza o evento para o contrato interno `WebhookPayload`.
3. O evento vira job ARQ.
4. O worker executa `ProcessWebhookService`.
5. O evento e gravado em `webhook_events`.

## Eventos relevantes hoje

- `PAYMENT_CONFIRMED`
- `PAYMENT_RECEIVED`
- `PAYMENT_OVERDUE`
- `PAYMENT_REFUNDED`
- `PAYMENT_DELETED`
- `SUBSCRIPTION_INACTIVATED`
- `SUBSCRIPTION_DELETED`

Eventos de pagamento sem `subscription` sao tratados como pagamentos avulsos. O Billing Core procura o pagamento local por `provider_payment_id` e aplica a transicao correspondente:

- `PAYMENT_CONFIRMED`: `confirmed`
- `PAYMENT_RECEIVED`: `paid`
- `PAYMENT_OVERDUE`: `overdue`
- `PAYMENT_REFUNDED` e `PAYMENT_CHARGEBACK_REQUESTED`: `refunded` quando aplicavel
- `PAYMENT_DELETED`: `canceled` quando ainda pendente ou vencido

Quando o gateway confirma cancelamento por webhook:

- se a assinatura estiver `active` ou `pending`, o core a marca como `canceled`
- se a assinatura estiver `cancellation_pending`, o evento apenas conclui o estado local esperado
- se a assinatura ja estiver `canceled`, o processamento permanece idempotente

## Idempotencia

- replay curto na borda HTTP via Redis
- duplicatas conhecidas na janela de replay recebem resposta `200` com `{"received": true, "duplicate": true}`
- idempotencia de negocio via `WebhookEvent.event_id`
- lock adicional de processamento no worker
- compatibilidade com jobs locais de cancelamento chegando antes ou depois do webhook

## Webhook interno Neectify

O Billing Core tambem pode enviar webhook interno assinado por HMAC para sistemas Neectify.

Header usado:

- `X-Webhook-Signature-256`
- `X-Webhook-Id`
- `X-Webhook-Event`

Configuracao:

- `INTERNAL_WEBHOOK_SIGNATURE`

## Pontos a validar antes de producao plena

- formato canônico final dos eventos internos
- politica de retry para webhook interno
- contrato publico por tipo de sistema consumidor
