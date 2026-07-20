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
- `payments:create` (Criar checkouts avulsos)
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
> Assinaturas (`POST /v1/subscriptions`) precisam de `customer_provider_id`.
> O checkout criado por `POST /v1/payments` coleta os dados do comprador no Asaas e não recebe customer prévio.
>
> Para assinaturas:
> 1. Salve o `provider_customer_id` retornado no banco de dados local do seu SaaS, associado ao cadastro do usuário.
> 2. Antes de criar uma assinatura, verifique se o usuário já possui este ID.
> 3. Caso não possua, chame `POST /v1/customers` passando o CPF/CNPJ do usuário e salve o ID recebido localmente para a assinatura.
> 4. Caso já possua, **reutilize o ID salvo** diretamente no payload de `POST /v1/subscriptions`.

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

Cria um checkout Asaas assíncrono e idempotente. Não cria uma cobrança direta e não recebe `customer_provider_id`, `billing_type` ou data de vencimento.

#### Auth

- obrigatória
- scope: `payments:create`
- header obrigatório: `Idempotency-Key`

#### Payload

```json
{
  "value": "72.00",
  "description": "Créditos NF-e - pack_100",
  "system": "marketfy",
  "system_payment_id": "550e8400-e29b-41d4-a716-446655440000",
  "webhook_link": "https://api-marketfy.neectify.com/api/v1/webhooks/billing-core",
  "minutes_to_expire": 30,
  "items": [{
    "external_reference": "550e8400-e29b-41d4-a716-446655440000",
    "name": "100 créditos NF-e",
    "description": "Créditos para emissão fiscal",
    "quantity": 1,
    "value": "72.00"
  }],
  "success_url": "https://app.marketfy.com/billing/success",
  "cancel_url": "https://app.marketfy.com/billing/cancel",
  "expired_url": "https://app.marketfy.com/billing/expired"
}
```

`minutes_to_expire` aceita de 10 a 1440. A soma de `items` deve ser igual a `value`, e cada item possui sua própria `external_reference`. A referência externa do checkout é calculada como `checkout:{system}:{system_payment_id}`. As três URLs de retorno devem usar host presente em `ALLOWED_CHECKOUT_REDIRECT_HOSTS`.

#### Resposta `202`

```json
{
  "job_id": "job-123",
  "message": "Checkout enviado para criação."
}
```

Quando concluído, `GET /v1/jobs/{job_id}` inclui `payment_id`, `checkout_url` e `payment_status` no resultado.

#### Regras importantes

- A mesma `Idempotency-Key` com o mesmo payload retorna o mesmo `job_id`; com payload distinto retorna `409 conflict`.
- O checkout Asaas usa PIX e cartão; os dados do comprador são coletados pelo Asaas.
- As URLs de retorno só guiam a interface. Elas **nunca** concedem acesso, crédito ou outro benefício.
- A confirmação financeira é o webhook Asaas `CHECKOUT_PAID`, que gera a atualização interna assinada.

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
