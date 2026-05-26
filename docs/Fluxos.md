# Fluxos Operacionais - Billing Core

## Fluxo de assinatura

1. Sistema interno chama `POST /v1/subscriptions`.
2. Auth e scope sao validados.
3. `Idempotency-Key` e validada no Redis.
4. O request vira job ARQ.
5. O worker busca o customer pelo `customer_provider_id`.
6. `CreateSubscription` consulta duplicidade por `system_sub_id + system`.
7. Se nao existir, chama o gateway.
8. Persiste `Subscription`.
9. Persiste o primeiro `Payment`.
10. O consumidor acompanha por `GET /v1/jobs/{job_id}`.

## Fluxo de cancelamento de assinatura

1. Sistema interno chama `POST /v1/subscriptions/{subscription_id}/cancel`.
2. Auth, scope `subscriptions:cancel`, rate limit e `Idempotency-Key` sao validados.
3. A API verifica se a assinatura existe e pertence ao `X-System`.
4. O request vira job ARQ e retorna `job_id`.
5. O worker executa `CancelSubscription`.
6. A assinatura vai para `cancellation_pending` antes da chamada externa.
7. O caso de uso consulta `gateway_operations` e o estado remoto para retry seguro.
8. O gateway cancela a assinatura quando necessario.
9. O core marca a assinatura como `canceled`, registra `cancelled_at`, `cancellation_reason` e `cancellation_job_id`.
10. Um webhook tardio do gateway continua idempotente e nao quebra o estado final local.

## Fluxo de pagamento recebido por webhook

1. Asaas envia evento.
2. A API valida secret e replay.
3. O adapter normaliza o payload.
4. O worker executa `ProcessWebhookService`.
5. O evento tecnico e consultado em `webhook_events`.
6. O pagamento e criado ou atualizado.
7. A assinatura e marcada como ativa quando aplicavel.
8. O evento e marcado como processado.

## Fluxo de falha

### Falha de validacao na borda

- retorna erro HTTP padrao
- nao enfileira job
- inclui `request_id`

### Falha de negocio no worker

- job e marcado como `failed`
- erro fica salvo nos metadados
- job entra na dead-letter logica em Redis

### Falha transitoria no worker

- job pode ficar como `retrying`
- tentativa atual e registrada

## Reconciliacao

Hoje a reconciliacao e principalmente operacional:

- consultar `payments`, `subscriptions` e `webhook_events`
- comparar `provider_payment_id` e `gateway_subscription_id`
- conferir dead-letter e metadados de job

Ponto a validar:

- job automatico de reconciliacao periodica com o gateway ainda nao existe
- operacoes em `requires_reconciliation` exigem acao operacional antes de nova tentativa manual
