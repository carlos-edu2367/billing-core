# API - Billing Core

## Autenticacao interna

As rotas internas exigem:

- `X-System`
- `X-API-Key`

O par precisa existir em `INTERNAL_API_CLIENTS`.

### Scopes atuais

- `subscriptions:create`
- `jobs:read`
- `metrics:read`

## Headers relevantes

| Header | Uso |
| --- | --- |
| `X-System` | identifica o sistema Neectify chamador |
| `X-API-Key` | autentica o cliente interno |
| `Idempotency-Key` | exigido na criacao de assinatura |
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
- `internal_server_error`
