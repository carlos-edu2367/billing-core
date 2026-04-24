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

- `PAYMENT_RECEIVED`
- `SUBSCRIPTION_INACTIVATED`
- `SUBSCRIPTION_DELETED`

## Idempotencia

- replay curto na borda HTTP via Redis
- idempotencia de negocio via `WebhookEvent.event_id`
- lock adicional de processamento no worker

## Webhook interno Neectify

O Billing Core tambem pode enviar webhook interno assinado por HMAC para sistemas Neectify.

Header usado:

- `X-Webhook-Signature-256`

Configuracao:

- `INTERNAL_WEBHOOK_SIGNATURE`

## Pontos a validar antes de producao plena

- formato canônico final dos eventos internos
- politica de retry para webhook interno
- contrato publico por tipo de sistema consumidor
