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
6. Para pagamento avulso, o core tenta localizar por `payment.id`.
7. Se o pagamento foi criado por payment link, o Asaas envia `pay_xxx` no webhook enquanto o registro local nasceu com `pml_xxx`; nesse caso o core faz fallback por `externalReference`.
8. Quando encontra por `externalReference`, o core atualiza `provider_payment_id` para o `pay_xxx` real.
9. O pagamento e criado ou atualizado.
10. A assinatura e marcada como ativa quando aplicavel.
11. O evento e marcado como processado.

## Fluxo de checkout avulso via payment link

1. Sistema interno chama `POST /v1/payment-links`.
2. Auth, scope `payments:create`, rate limit e `Idempotency-Key` sao validados.
3. O request vira job ARQ `create_payment_link_worker`.
4. `CreatePaymentLink` consulta duplicidade por `system_payment_id + system`.
5. Se nao existir, monta `externalReference` no formato `payment:{system}:{system_payment_id}`.
6. O provider Asaas chama `POST /v3/paymentLinks` com `chargeType=DETACHED`, `billingType=UNDEFINED` e `dueDateLimitDays`.
7. O core persiste `Payment` local com `provider_payment_id=pml_xxx`, `checkout_link=url`, `payment_status=pending` e `webhook_link`.
8. O consumidor acompanha por `GET /v1/jobs/{job_id}`.
9. Ao completar, o resultado do job contem `payment_id` e `checkout_url`.
10. O usuario paga no checkout Asaas.
11. O webhook do Asaas chega com a cobranca real `pay_xxx` e o mesmo `externalReference`.
12. O core atualiza o pagamento local e envia webhook interno `PAYMENT_STATUS_UPDATED` para o produto.

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
