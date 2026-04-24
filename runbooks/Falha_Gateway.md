# Runbook - Falha de Gateway

## Quando usar

- timeout no Asaas
- erro 5xx frequente no provider
- webhook deixando de chegar
- criacao de assinatura falhando em lote

## Sintomas

- jobs `retrying` ou `failed` em `create_subscription_worker`
- aumento de erro de webhook
- fila crescendo sem drenagem adequada

## Passos

1. Confirmar se o problema e no gateway e nao em banco ou Redis.
2. Procurar erros de integracao nos logs estruturados.
3. Conferir token e secret configurados.
4. Validar se o gateway esta com incidente oficial.

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

## Pos-incidente

- reconciliar assinaturas criadas no gateway mas nao persistidas localmente
- reconciliar pagamentos recebidos por webhook atrasado
- revisar necessidade de circuit breaker e reconciliacao automatica
