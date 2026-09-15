# Runbook - Falha de Gateway

## Quando usar

- timeout no Asaas ou no Mercado Pago
- erro 5xx frequente no provider
- webhook deixando de chegar
- criacao de assinatura falhando em lote

## Sintomas

- jobs `retrying` ou `failed` em `create_subscription_worker`, `create_checkout_worker` ou `process_gateway_notification`
- `error_code` no formato `AsaasAPIError_<status>` ou `MercadoPagoAPIError_<status>` nos jobs (ambos derivam de `GatewayAPIError`; 4xx e terminal, 5xx tem retry)
- aumento de erro de webhook em `/v1/webhooks/asaas` ou `/v1/webhooks/mercadopago`
- fila crescendo sem drenagem adequada
- (Mercado Pago) checkouts presos em `pending` alem do prazo esperado: verificar se o cron `sync_pending_checkouts_worker` esta rodando (a cada 5 minutos)

## Passos

1. Confirmar se o problema e no gateway e nao em banco ou Redis.
2. Identificar qual gateway esta envolvido (`gateway_provider`/`gateway` no registro, ou o `provider` no `error_code`/metadados do job).
3. Procurar erros de integracao nos logs estruturados.
4. Conferir token e secret configurados (`ASAAS_API_TOKEN`/`ASAAS_WEBHOOK_SECRET` ou `MERCADOPAGO_ACCESS_TOKEN`/`MERCADOPAGO_WEBHOOK_SECRET`, conforme o gateway).
5. Validar se o gateway esta com incidente oficial.

## Mitigacao

- reduzir temporariamente o volume de chamadas no sistema consumidor
- evitar reenvio manual em massa sem idempotencia
- preservar `job_id`, `system_sub_id` e `provider_payment_id`

## Decisao operacional

### Falha transitoria

- manter retries
- acompanhar backlog

### Falha prolongada

- pausar novas criacoes no lado consumidor
- priorizar preservacao de consistencia sobre throughput
- (se o gateway padrao for `mercadopago` e o incidente for isolado a ele) considerar rollback temporario trocando `DEFAULT_GATEWAY_PROVIDER` para `asaas` e reiniciando API e worker — registros ja criados no gateway anterior continuam sendo processados normalmente

## Pos-incidente

- reconciliar assinaturas criadas no gateway mas nao persistidas localmente
- reconciliar pagamentos recebidos por webhook atrasado
- revisar necessidade de circuit breaker e reconciliacao automatica
