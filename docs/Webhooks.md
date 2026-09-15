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

## Webhook recebido do Mercado Pago

Endpoint atual:

- `POST /v1/webhooks/mercadopago`

O Mercado Pago nao envia o estado do recurso na notificacao — so `type` (ou `topic`) e `data.id`. Por isso o processamento e diferente do Asaas:

1. O payload cru (so com o id do recurso) e validado e vira job `workers:tasks.process_gateway_notification`.
2. O worker chama `MercadoPagoProvider.resolve_webhook`, que busca o recurso completo na API do Mercado Pago (`GET /v1/payments/{id}`, `GET /authorized_payments/{id}` ou `GET /preapproval/{id}`, conforme o topico) e traduz para `WebhookPayload`.
3. Se o resultado for `None` (por exemplo, um `payment` rejeitado ou um tópico não tratado), o job termina como `ignored` sem novo processamento.
4. Caso contrario, o payload resolvido segue o mesmo `process_webhook` usado pelo Asaas.

Topicos tratados: `payment`, `subscription_authorized_payment`, `subscription_preapproval`. Outros topicos (`topic_merchant_order_wh`, `topic_chargebacks_wh` etc.) sao recebidos mas ignorados por `resolve_webhook`.

### Validacoes aplicadas

- header `x-signature` obrigatorio, no formato `ts=<timestamp>,v1=<hash>`
- HMAC SHA256 do manifest `id:<data.id>;request-id:<x-request-id>;ts:<ts>;` (valores ausentes saem do manifest; `data.id` alfanumerico vai em minusculas) com `MERCADOPAGO_WEBHOOK_SECRET`
- corpo obrigatorio e precisa trazer `data.id`
- protecao contra replay baseada em hash do corpo, igual ao Asaas
- rate limiting

### Expiracao de checkout

O Mercado Pago nao notifica quando uma preferencia do Checkout Pro expira. Por isso existe o cron `sync_pending_checkouts_worker`, que roda a cada 5 minutos, busca checkouts `pending` do gateway `mercadopago` e aplica `PAID`/`EXPIRED`/`CANCELED` consultando `GET /checkout/preferences/{id}` e `GET /v1/payments/search`.

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
