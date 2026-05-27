# API - Billing Core

## Autenticacao interna

As rotas internas exigem:

- `X-System`
- `X-API-Key`

O par precisa existir em `INTERNAL_API_CLIENTS`.

### Scopes atuais

- `customers:create` (Criar/Consultar clientes no Asaas)
- `subscriptions:create` (Criar assinaturas)
- `subscriptions:cancel` (Cancelar assinaturas)
- `payments:create` (Criar pagamentos avulsos e links de checkout)
- `payments:read` (Consultar pagamentos locais)
- `jobs:read` (Consultar estado de processamento assíncrono)
- `metrics:read` (Acesso a métricas da aplicação)

## Headers relevantes

| Header | Uso |
| --- | --- |
| `X-System` | identifica o sistema Neectify chamador |
| `X-API-Key` | autentica o cliente interno |
| `Idempotency-Key` | exigido nas operacoes assincronas de escrita, como criacao e cancelamento |
| `X-Request-ID` | opcional na entrada; retornado em toda resposta |
| `asaas-access-token` | validacao de webhook Asaas |

## Endpoints

### `POST /v1/customers`

Registra um novo cliente no provedor de pagamento (Asaas). Este endpoint é **idempotente por CPF/CNPJ**: se o cliente já estiver registrado no Asaas, ele retornará o mesmo `provider_customer_id` existente sem duplicar o cadastro.

> [!IMPORTANT]
> **Quando usar `POST /v1/customers`:**
> Assinaturas (`POST /v1/subscriptions`) e pagamentos avulsos legados (`POST /v1/payments`) ainda precisam de `customer_provider_id`.
> Para checkout avulso sem customer previo, use `POST /v1/payment-links`; nesse fluxo o Asaas coleta os dados do comprador no checkout.
>
> Para fluxos que ainda usam customer:
> 1. Salve o `provider_customer_id` retornado no banco de dados local do seu SaaS, associado ao cadastro do usuário.
> 2. Antes de criar uma cobrança avulsa, verifique se o usuário já possui este ID.
> 3. Caso não possua, chame `POST /v1/customers` passando o CPF/CNPJ do usuário, salve o ID recebido localmente, e use-o na chamada de pagamento.
> 4. Caso já possua, **reutilize o ID salvo** diretamente no payload de `POST /v1/payments`.

#### Auth

- obrigatória
- scope: `customers:create`

#### Body

```json
{
  "nome_completo": "João Silva",
  "email": "joao@exemplo.com",
  "cpf": "390.533.447-05",
  "system_customer_id": "user_42",
  "system": "marketfy"
}
```

*Nota: é obrigatório e exclusivo o envio de `cpf` ou `cnpj`.*

#### Resposta `201`

```json
{
  "provider_customer_id": "cus_000005113076"
}
```

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

Cria um pagamento avulso de forma assincrona e idempotente usando um customer Asaas ja existente.

Para checkout avulso sem cadastro previo do comprador, prefira `POST /v1/payment-links`.

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

### `POST /v1/payment-links`

Cria um link de checkout Asaas de forma assincrona e idempotente. Este fluxo nao exige `customer_provider_id`: o comprador informa nome, CPF/CNPJ e e-mail diretamente no checkout do Asaas.

Use este endpoint para compras avulsas em que o produto nao deve criar customer antes do pagamento, como checkout de creditos fiscais do Marketfy.

#### Auth

- obrigatoria
- scope: `payments:create`
- header obrigatorio: `Idempotency-Key`

#### Payload

```json
{
  "value": "72.00",
  "billing_type": "UNDEFINED",
  "description": "Creditos NF-e - pack_100",
  "due_date_limit_days": 3,
  "system": "marketfy",
  "system_payment_id": "550e8400-e29b-41d4-a716-446655440000",
  "webhook_link": "https://api-marketfy.neectify.com/api/v1/webhooks/billing-core"
}
```

#### Campos

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `value` | decimal | sim | Valor do checkout em reais (> 0) |
| `billing_type` | enum | nao | `UNDEFINED` por padrao; permite PIX, boleto ou cartao no checkout Asaas |
| `description` | string | sim | Nome/descricao exibida no checkout (max. 255 caracteres) |
| `due_date_limit_days` | int | nao | Dias para pagamento apos geracao da cobranca; padrao `3` |
| `system` | enum | sim | Deve ser igual ao `X-System` autenticado |
| `system_payment_id` | string | sim | ID interno unico do pedido/pacote no produto chamador |
| `webhook_link` | string | sim | Endpoint HTTPS do produto para receber atualizacoes normalizadas |

#### Resposta `202`

```json
{
  "job_id": "job-123",
  "message": "Checkout enviado para criacao."
}
```

Quando o job completar, `GET /v1/jobs/{job_id}` retorna:

```json
{
  "job_id": "job-123",
  "status": "completed",
  "result": {
    "payment_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "checkout_url": "https://www.asaas.com/c/pml_000005219613",
    "payment_status": "pending"
  }
}
```

#### Regras importantes

- `customer_provider_id` nao deve ser enviado.
- A mesma `Idempotency-Key` com o mesmo payload retorna o mesmo `job_id`.
- A mesma `Idempotency-Key` com payload diferente retorna `409 conflict`.
- `billing_type=DEBIT_CARD` nao e aceito.
- O Billing Core cria `externalReference` no formato `payment:{system}:{system_payment_id}` para correlacionar o webhook do Asaas.
- O registro local nasce com `provider_payment_id = pml_xxx`. Quando o webhook de pagamento chega com `pay_xxx`, o Billing Core localiza por `externalReference` e atualiza o `provider_payment_id`.

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
