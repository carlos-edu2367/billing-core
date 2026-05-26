# API - Billing Core

## Autenticacao interna

As rotas internas exigem:

- `X-System`
- `X-API-Key`

O par precisa existir em `INTERNAL_API_CLIENTS`.

### Scopes atuais

- `subscriptions:create`
- `subscriptions:cancel`
- `jobs:read`
- `metrics:read`

## Headers relevantes

| Header | Uso |
| --- | --- |
| `X-System` | identifica o sistema Neectify chamador |
| `X-API-Key` | autentica o cliente interno |
| `Idempotency-Key` | exigido nas operacoes assincronas de escrita, como criacao e cancelamento |
| `X-Request-ID` | opcional na entrada; retornado em toda resposta |
| `asaas-access-token` | validacao de webhook Asaas |

## Endpoints

### `POST /v1/subscriptions`

Cria uma solicitacao de assinatura e retorna um `job_id`.

#### Auth

- obrigatoria
- scope: `subscriptions:create`

#### Body

```json
{
  "customer_provider_id": "cus_123",
  "value": "129.90",
  "subscription_type": "YEARLY",
  "next_due_date": "2026-05-01",
  "description": "Plano Pro ",
  "system": "neectify_shop",
  "system_sub_id": "sub_shop_001",
  "expires_at": "2027-05-01T00:00:00Z",
  "webhook_link": "https://hooks.neectify.local/billing/subscription"
}
```

#### Resposta `202`

```json
{
  "job_id": "job-123",
  "message": "Assinatura enviada para processamento."
}
```

### `POST /v1/webhooks/asaas`

Recebe evento do Asaas, valida o secret e enfileira o processamento.

#### Auth

- via `asaas-access-token`

#### Resposta `202`

```json
{
  "job_id": "job-456",
  "message": "Webhook recebido para processamento."
}
```

### `POST /v1/subscriptions/{subscription_id}/cancel`

Solicita o cancelamento assincrono de uma assinatura existente.

#### Auth

- obrigatoria
- scope: `subscriptions:cancel`

#### Path param

- `subscription_id`: UUID interno da assinatura

#### Body

```json
{
  "reason": "Cancelado a pedido do cliente apos downgrade."
}
```

#### Resposta `202`

```json
{
  "job_id": "job-789",
  "message": "Cancelamento enviado para processamento."
}
```

#### Regras importantes

- a assinatura precisa pertencer ao `X-System` autenticado
- a mesma `Idempotency-Key` com o mesmo payload retorna o mesmo `job_id`
- a mesma `Idempotency-Key` com payload diferente retorna `409 conflict`
- assinaturas ja canceladas retornam `409 conflict`
- assinatura inexistente ou de outro sistema retorna `404 not_found`
- `reason` e opcional e limitado a 500 caracteres

### `GET /v1/jobs/{job_id}`

Consulta o estado do job associado ao sistema autenticado.

#### Auth

- obrigatoria
- scope: `jobs:read`

#### Resposta `200`

```json
{
  "job_id": "job-123",
  "status": "processing",
  "job_name": "create_subscription_worker",
  "attempt": 1,
  "max_tries": 3,
  "request_id": "8f0873ff-9c88-49cb-9f0a-e7633e6f8a4b",
  "created_at": "2026-04-24T00:00:00+00:00",
  "started_at": "2026-04-24T00:00:01+00:00",
  "finished_at": null,
  "error_code": null,
  "error_message": null
}
```

### `POST /v1/payments`

Cria um pagamento avulso de forma assincrona e idempotente.

#### Auth

- obrigatoria
- scope: `payments:create`
- header obrigatorio: `Idempotency-Key`

#### Payload

```json
{
  "customer_provider_id": "cus_123",
  "value": "79.90",
  "billing_type": "UNDEFINED",
  "due_date": "2026-06-10",
  "description": "Pedido 123",
  "system": "neectify_shop",
  "system_payment_id": "order-123",
  "webhook_link": "https://hooks.neectify.local/billing/payment"
}
```

Para permitir que o pagador escolha a forma de pagamento, envie `billing_type=UNDEFINED`.
O endpoint regular de cobranca do Asaas nao aceita multiplos `billingType` em uma unica cobranca.

#### Resposta `202`

```json
{
  "job_id": "job-123",
  "message": "Pagamento enviado para processamento."
}
```

O resultado do job contem `payment_id`, `checkout_url`, `payment_status`, `billing_type`, `value` e `due_date`.

### `GET /v1/payments/{payment_id}`

Consulta somente o estado local do pagamento. Esta rota nunca consulta o Asaas.

#### Auth

- obrigatoria
- scope: `payments:read`

#### Regras

- o pagamento precisa pertencer ao `X-System` autenticado
- chamadas para o mesmo pagamento e sistema devem respeitar intervalo minimo de 10 segundos
- chamadas antes do intervalo retornam `429` com `Retry-After: 10`

### Endpoints operacionais

- `GET /health`
- `GET /ready`
- `GET /live`
- `GET /metrics` (autenticado com `metrics:read`)

## Erros

O envelope padrao hoje e:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Dados invalidos.",
    "request_id": "8f0873ff-9c88-49cb-9f0a-e7633e6f8a4b"
  }
}
```

Codigos comuns:

- `unauthorized`
- `forbidden`
- `validation_error`
- `conflict`
- `not_found`
- `rate_limit_exceeded`
- `internal_server_error`

## Estados de assinatura

- `pending`
- `active`
- `cancellation_pending`
- `canceled`
