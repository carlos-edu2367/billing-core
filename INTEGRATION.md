# Guia de Integração — Billing Core

Documentação para integrar um produto Neectify ao Billing Core.

> **Base URL (produção):** `https://api.billing.neectify.com`

---

## Índice

1. [Visão geral do fluxo](#1-visão-geral-do-fluxo)
2. [Configuração no Billing Core](#2-configuração-no-billing-core)
3. [Criar cliente](#3-criar-cliente)
4. [Criar assinatura](#4-criar-assinatura)
5. [Consultar job](#5-consultar-job)
6. [Receber webhook interno](#6-receber-webhook-interno)
7. [Checklist de go-live](#7-checklist-de-go-live)
8. [Referência de erros](#8-referência-de-erros)

---

## 1. Visão geral do fluxo

```
Seu produto                  Billing Core                        Asaas
     │                            │                                │
     │─ POST /v1/customers ──────>│─ POST /v3/customers ─────────>│
     │<─ { provider_customer_id } │<─ { id: "cus_xxx" } ──────────│
     │                            │                                │
     │─ POST /v1/subscriptions ──>│─ POST /v3/subscriptions ─────>│
     │<─ { job_id }               │<─ { id: "sub_xxx" } ──────────│
     │                            │                                │
     │─ GET /v1/jobs/{job_id} ───>│                                │
     │<─ { status: "completed" }  │                                │
     │                            │                                │
     │<── POST /billing/webhook ──│  (pagamento confirmado)        │
     │  { event, expires_at, … }  │                                │
```

A criação de assinatura é **assíncrona**: o `POST /v1/subscriptions` retorna imediatamente um `job_id`. Use o `GET /v1/jobs/{job_id}` para saber quando terminou.

---

## 2. Configuração no Billing Core

Antes de fazer qualquer chamada, três coisas precisam estar configuradas pelo time do Billing Core:

### 2.1 Registrar o sistema no enum `System`

**Arquivo:** `app/domain/enums/system.py`

```python
class System(Enum):
    MARKETFY      = "marketfy"
    NEECTIFY_SHOP = "neectify_shop"
    NEECTIFY_FOOD = "neectify_food"   # ✅ já adicionado
```

> Para novos produtos, adicione uma entrada seguindo o mesmo padrão.

### 2.2 Credenciais no ambiente de produção

Adicionar a entrada do produto no `INTERNAL_API_CLIENTS`:

```json
{
  "neectify_food": {
    "api_key": "<api-key-gerada-para-o-food>",
    "scopes": ["customers:create", "subscriptions:create", "jobs:read"]
  }
}
```

### 2.3 Liberar o host do webhook interno

```env
ALLOWED_INTERNAL_WEBHOOK_HOSTS=["api.food.neectify.com", "api.shop.neectify.com"]
```

O Billing Core rejeita qualquer `webhook_link` cujo hostname não esteja nessa lista.

---

## 3. Criar cliente

Toda assinatura requer um cliente previamente cadastrado. Chame esse endpoint **uma vez por usuário** e persista o `provider_customer_id` retornado.

> **Escopo necessário:** `customers:create` — certifique-se de que ele está na lista `scopes` do `INTERNAL_API_CLIENTS` do seu sistema.

### Request

```http
POST https://api.billing.neectify.com/v1/customers
Content-Type: application/json
X-System:     neectify_food
X-API-Key:    <sua-api-key>
```

```json
{
  "nome_completo":      "João Silva",
  "email":              "joao@exemplo.com",
  "cpf":                "390.533.447-05",
  "system_customer_id": "user_42",
  "system":             "neectify_food"
}
```

> Use `cnpj` no lugar de `cpf` para pessoas jurídicas. Exatamente um dos dois deve ser enviado.

### Response `201`

```json
{
  "provider_customer_id": "cus_000005113076"
}
```

### Comportamento de idempotência

Se um cliente com o mesmo CPF/CNPJ já existir no Asaas, o Billing Core reutiliza o cadastro existente e retorna o mesmo `provider_customer_id`. Seguro chamar mais de uma vez.

---

## 4. Criar assinatura

### Request

```http
POST https://api.billing.neectify.com/v1/subscriptions
Content-Type:    application/json
X-System:        neectify_food
X-API-Key:       <sua-api-key>
Idempotency-Key: <chave-unica-por-tentativa>
```

```json
{
  "customer_provider_id": "cus_000005113076",
  "value":                "49.90",
  "subscription_type":    "MONTHLY",
  "next_due_date":        "2026-06-01",
  "description":          "Plano Mensal - Neectify Food",
  "system":               "neectify_food",
  "system_sub_id":        "sub_food_user42_mensal",
  "expires_at":           "2026-07-01T00:00:00Z",
  "webhook_link":         "https://api.food.neectify.com/billing/webhook"
}
```

### Campos

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `customer_provider_id` | string | sim | ID Asaas retornado pelo `/v1/customers` |
| `value` | decimal | sim | Valor em reais (> 0) |
| `subscription_type` | enum | sim | `MONTHLY`, `SEMIANNUALLY` ou `YEARLY` |
| `next_due_date` | date | não | Data da primeira cobrança (`YYYY-MM-DD`). Se omitido, usa a data atual. |
| `description` | string | sim | Descrição interna (máx. 255 caracteres) |
| `system` | enum | sim | Deve ser igual ao sistema do header `X-System` |
| `system_sub_id` | string | sim | Seu ID único da assinatura |
| `expires_at` | datetime ISO 8601 | sim | Expiração inicial. Atualizado automaticamente a cada pagamento. |
| `webhook_link` | string | sim | Endpoint HTTPS que receberá os eventos normalizados |

### Header `Idempotency-Key`

Obrigatório. Use um valor único por tentativa de criação (ex.: `sub_food_user42_mensal_2026_05`). Se chamar novamente com a mesma chave e o mesmo payload, recebe o mesmo `job_id` sem criar uma segunda assinatura.

### Response `202`

```json
{
  "job_id":  "arq:job:7f3a1c...",
  "message": "Assinatura enviada para processamento."
}
```

---

## 5. Consultar job

Use o `job_id` retornado pelo `/v1/subscriptions` para acompanhar o processamento.

### Request

```http
GET https://api.billing.neectify.com/v1/jobs/arq:job:7f3a1c...
X-System:  neectify_food
X-API-Key: <sua-api-key>
```

### Response `200`

```json
{
  "job_id":      "arq:job:7f3a1c...",
  "status":      "completed",
  "job_name":    "create_subscription_worker",
  "attempt":     1,
  "max_tries":   3,
  "created_at":  "2026-05-20T10:00:00Z",
  "started_at":  "2026-05-20T10:00:01Z",
  "finished_at": "2026-05-20T10:00:02Z",
  "error_code":  null,
  "error_message": null
}
```

### Status possíveis

| Status | Significado | Ação |
|---|---|---|
| `queued` | Aguardando worker disponível | Re-consultar em alguns segundos |
| `processing` | Em execução | Aguardar |
| `completed` | Sucesso — assinatura criada | Prosseguir; aguardar webhook de pagamento |
| `retrying` | Falha transitória, tentando novamente | Aguardar |
| `failed` | Falha terminal | Verificar `error_message` e alertar o time |

### Estratégia de polling recomendada

```
T+1s  → consultar
T+3s  → consultar
T+10s → consultar
T+30s → consultar
T+60s → tratar como timeout e acionar suporte
```

---

## 6. Receber webhook interno

Após cada evento relevante de pagamento ou assinatura, o Billing Core envia um `POST` para o `webhook_link` informado na criação da assinatura.

### Endpoint esperado no seu produto

```
POST https://api.food.neectify.com/billing/webhook
```

### Payload recebido

```json
{
  "event":                   "PAYMENT_RECEIVED",
  "subscription_id":         "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "subscription_expires_at": "2026-07-01",
  "payment_date":            "2026-06-01"
}
```

### Eventos

| `event` | Quando dispara | `payment_date` |
|---|---|---|
| `PAYMENT_RECEIVED` | Pagamento confirmado no Asaas | Preenchido com a data do pagamento |
| `SUBSCRIPTION_INACTIVATED` | Assinatura cancelada ou inativada no Asaas | `null` |

### Validar a assinatura HMAC

O Billing Core assina cada requisição com `HMAC-SHA256`. **Valide sempre antes de processar** — rejeite com `401` se a assinatura não bater.

O segredo compartilhado é o valor de `INTERNAL_WEBHOOK_SIGNATURE` configurado no Billing Core. Solicite ao time de infra.

```python
# Python / FastAPI
import hmac, hashlib, base64, json
from fastapi import Request, Response

BILLING_WEBHOOK_SECRET = "segredo-compartilhado-com-o-billing-core"

def verify_billing_signature(raw_body: bytes, signature_header: str) -> bool:
    # O Billing Core serializa o payload com sort_keys + sem espaços
    payload_normalized = json.dumps(
        json.loads(raw_body),
        sort_keys=True,
        separators=(",", ":")
    )
    digest = hmac.new(
        BILLING_WEBHOOK_SECRET.encode("utf-8"),
        payload_normalized.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)

@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Webhook-Signature-256", "")

    if not verify_billing_signature(raw, signature):
        return Response(status_code=401)

    body = json.loads(raw)
    event = body["event"]
    subscription_id = body["subscription_id"]
    expires_at = body["subscription_expires_at"]   # "YYYY-MM-DD"
    payment_date = body.get("payment_date")        # "YYYY-MM-DD" ou null

    if event == "PAYMENT_RECEIVED":
        # Liberar/renovar acesso do usuário até expires_at
        await activate_subscription(subscription_id, expires_at)

    elif event == "SUBSCRIPTION_INACTIVATED":
        # Revogar acesso
        await deactivate_subscription(subscription_id)

    # Responda 2xx em até 30s — qualquer coisa fora disso gera retry
    return Response(status_code=200)
```

```typescript
// Node.js / Express
import crypto from "crypto";

const BILLING_WEBHOOK_SECRET = "segredo-compartilhado-com-o-billing-core";

function verifyBillingSignature(rawBody: Buffer, signatureHeader: string): boolean {
  const payloadNormalized = JSON.stringify(JSON.parse(rawBody.toString()), Object.keys(JSON.parse(rawBody.toString())).sort());
  const digest = crypto
    .createHmac("sha256", BILLING_WEBHOOK_SECRET)
    .update(payloadNormalized)
    .digest("base64");
  return crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(signatureHeader));
}

app.post("/billing/webhook", express.raw({ type: "application/json" }), (req, res) => {
  const signature = req.headers["x-webhook-signature-256"] as string;

  if (!verifyBillingSignature(req.body, signature)) {
    return res.status(401).send();
  }

  const body = JSON.parse(req.body.toString());

  if (body.event === "PAYMENT_RECEIVED") {
    // liberar acesso até body.subscription_expires_at
  } else if (body.event === "SUBSCRIPTION_INACTIVATED") {
    // revogar acesso
  }

  res.status(200).send();
});
```

### Política de retry do Billing Core

Se o seu endpoint não responder `2xx` em 30 segundos, o Billing Core tentará novamente **até 5 vezes** com backoff progressivo. Após a 5ª falha, a entrega vai para dead letter e precisará de intervenção manual.

---

## 7. Checklist de go-live

```
CONFIGURAÇÃO NO BILLING CORE
[x] NEECTIFY_FOOD adicionado ao enum System
[ ] Entrada no INTERNAL_API_CLIENTS com api_key e escopos corretos
    escopos mínimos: customers:create, subscriptions:create, jobs:read
[ ] Host do webhook adicionado em ALLOWED_INTERNAL_WEBHOOK_HOSTS
    ex: api.food.neectify.com

CONFIGURAÇÃO NO SEU PRODUTO
[ ] provider_customer_id salvo no banco de dados por usuário
[ ] Endpoint HTTPS em api.food.neectify.com/billing/webhook implementado
[ ] Validação HMAC do X-Webhook-Signature-256 implementada e testada
[ ] Endpoint responde 2xx em até 30s

TESTES END-TO-END (em sandbox)
[ ] POST /v1/customers → retorna provider_customer_id
[ ] POST /v1/subscriptions com Idempotency-Key → retorna job_id
[ ] Repetir POST com mesma Idempotency-Key → retorna mesmo job_id
[ ] GET /v1/jobs/{job_id} → status "completed"
[ ] Webhook PAYMENT_RECEIVED recebido e assinatura validada
[ ] Acesso do usuário liberado/atualizado após PAYMENT_RECEIVED
[ ] Webhook SUBSCRIPTION_INACTIVATED recebido
[ ] Acesso revogado após SUBSCRIPTION_INACTIVATED
```

---

## 8. Referência de erros

| HTTP | `error.code` | Causa | Ação |
|---|---|---|---|
| `400` | `bad_request` | Payload malformado ou sem identificador | Corrigir o body |
| `401` | `unauthorized` | `X-API-Key` inválida ou ausente | Verificar credenciais |
| `403` | `forbidden` | Escopo insuficiente ou sistema divergente | Verificar `X-System` e escopos |
| `409` | `conflict` | Webhook duplicado na janela de replay | Ignorar — já foi processado |
| `413` | `payload_too_large` | Body acima do limite | Reduzir payload |
| `415` | `unsupported_media_type` | Content-Type não é `application/json` | Corrigir header |
| `422` | `validation_error` | Campo inválido (data no passado, host não permitido, etc.) | Ver `error.details` |
| `429` | `rate_limit_exceeded` | Muitas requisições | Aguardar e tentar com backoff |
| `500` | `internal_error` | Erro interno | Acionar suporte Neectify |

---

## Produtos Neectify

| Produto | Frontend | API |
|---|---|---|
| Neectify Food | `food.neectify.com` | `api.food.neectify.com` |
| Neectify Shop | `shop.neectify.com` | `api.shop.neectify.com` |
| Billing Core | — | `api.billing.neectify.com` |
