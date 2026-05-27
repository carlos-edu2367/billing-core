# Refatoração: Checkout via Payment Link (sem customer obrigatório)

**Data:** 2026-05-27  
**Status:** Planejado  
**Motivação:** O fluxo atual de pagamento avulso exige a criação prévia de um customer no Asaas antes de gerar a cobrança. Isso faz com que cobranças de teste — ou de qualquer admin logado — sejam geradas no nome do usuário do dono da conta Asaas, e não no nome do comprador real. O objetivo desta refatoração é usar **Payment Links** do Asaas (`POST /v3/paymentLinks`), onde o próprio checkout coleta os dados do comprador.

---

## 1. Problema atual

```
Marketfy                       Billing Core                     Asaas
   │                                │                              │
   │── POST /v1/customers ─────────>│── POST /v3/customers ───────>│
   │<── { provider_customer_id } ───│<── { id: "cus_xxx" } ────────│
   │                                │                              │
   │── POST /v1/payments ──────────>│── POST /v3/payments ────────>│
   │   { customer_provider_id }     │   { customer: "cus_xxx" }    │
   │<── { job_id } ─────────────────│<── { id: "pay_xxx",          │
   │                                │     invoiceUrl: "..." }      │
   │── GET /v1/jobs/{job_id} ──────>│                              │
   │<── { checkout_url } ───────────│                              │
```

**Problemas identificados:**
1. O `customer` no Asaas é criado com o CPF/nome do usuário do sistema — qualquer erro no cadastro gera a cobrança em nome errado.
2. A criação de customer é um passo síncrono extra antes do pagamento.
3. Todos os compradores precisam ter CPF/CNPJ previamente cadastrado no Marketfy antes de poder comprar.
4. Cobranças de teste usam o usuário admin da conta, gerando boletos no nome errado.

---

## 2. Solução: Payment Link do Asaas

O Asaas oferece o endpoint `POST /v3/paymentLinks` que cria uma URL de checkout gerenciada pelo próprio Asaas. O comprador preenche seus dados (CPF, nome, e-mail) diretamente na página do Asaas no momento do pagamento.

**Vantagens:**
- Nenhum customer precisa ser criado previamente.
- O checkout do Asaas valida CPF/CNPJ em tempo real.
- Funciona para qualquer forma de pagamento (`billingType: UNDEFINED` deixa o comprador escolher: PIX, boleto ou cartão).
- A cobrança gerada fica registrada no Asaas com os dados corretos do comprador.

### 2.1 Endpoint Asaas

```
POST https://api.asaas.com/v3/paymentLinks
```

**Request (campos relevantes):**

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `name` | string | sim | Nome do link (exibido no checkout) |
| `value` | number | sim | Valor em reais |
| `billingType` | enum | sim | `UNDEFINED` = comprador escolhe PIX/boleto/cartão |
| `chargeType` | enum | sim | `DETACHED` = cobrança avulsa única por acesso |
| `dueDateLimitDays` | int | não | Dias até vencimento da cobrança gerada (padrão: 3) |
| `description` | string | não | Descrição exibida no checkout |
| `externalReference` | string | não | **Referência interna — crucial para rastrear o pagamento** |

> `chargeType: DETACHED` → cada acesso ao link gera uma cobrança única.  
> `chargeType: RECURRENT` → gera assinatura (não usar neste fluxo).

**Response:**

```json
{
  "id": "pml_000005219613",
  "name": "Créditos NF-e — pack_100",
  "value": 72.00,
  "billingType": "UNDEFINED",
  "chargeType": "DETACHED",
  "url": "https://www.asaas.com/c/pml_000005219613",
  "active": true,
  "externalReference": "payment:marketfy:550e8400-e29b-41d4-a716-446655440000"
}
```

> **`url`** é a URL do checkout que deve ser exibida/redirecionada ao comprador.

### 2.2 Fluxo após o pagamento

Quando o comprador paga no checkout do Asaas:
1. Asaas cria automaticamente o customer (com os dados que o comprador digitou).
2. Asaas gera a cobrança (`pay_xxx`) vinculada ao payment link.
3. Asaas dispara webhook `PAYMENT_RECEIVED` para o endpoint configurado no billing_core.
4. O payload do webhook inclui `payment.externalReference` (nossa referência) e `payment.paymentLink` (ID do link).

---

## 3. Novo fluxo proposto

```
Marketfy                       Billing Core                     Asaas
   │                                │                              │
   │── POST /v1/payment-links ─────>│── POST /v3/paymentLinks ────>│
   │   { value, description, ... }  │   { name, value, ... }       │
   │<── { job_id } ─────────────────│<── { id, url } ──────────────│
   │                                │                              │
   │── GET /v1/jobs/{job_id} ──────>│                              │
   │<── { checkout_url } ───────────│                              │
   │                                │                              │
   │   [usuário acessa checkout_url e paga]                        │
   │                                │<── PAYMENT_RECEIVED webhook ─│
   │                                │    { payment.externalRef }   │
   │<── POST webhook_link ──────────│                              │
   │   { event: PAYMENT_STATUS_UPDATED, payment_id, status: PAID } │
```

**Diferenças-chave:**
- `POST /v1/payment-links` **não recebe `customer_provider_id`**.
- O billing_core cria o payment link no Asaas e **armazena o `url` retornado como `checkout_link`** na entidade `Payment`.
- O job result inclui `checkout_url`.
- O webhook de confirmação continua funcionando da mesma forma (via `externalReference`).

---

## 4. Mudanças no Billing Core

### 4.1 Asaas Provider — novo método

**Arquivo:** `app/infra/interfaces/asaas_provider.py`

Adicionar método `create_payment_link` à classe `AsaasProvider`:

```python
@dataclass
class CreatePaymentLinkGatewayResponse:
    payment_link_id: str   # ex: "pml_000005219613"
    checkout_url: str      # ex: "https://www.asaas.com/c/pml_000005219613"

# Em AsaasProvider:
async def create_payment_link(
    self,
    name: str,
    value: Decimal,
    billing_type: PaymentType,
    description: str,
    external_reference: str,
    due_date_limit_days: int = 3,
) -> CreatePaymentLinkGatewayResponse:
    payload = {
        "name": name,
        "value": float(value),
        "billingType": billing_type.value,
        "chargeType": "DETACHED",
        "dueDateLimitDays": due_date_limit_days,
        "description": description,
        "externalReference": external_reference,
    }
    response = await self.asaas.post("/paymentLinks", payload)
    return CreatePaymentLinkGatewayResponse(
        payment_link_id=response["id"],
        checkout_url=response["url"],
    )
```

Adicionar assinatura abstrata em `InterfaceGateway` (`app/application/interfaces/gateway_provider.py`).

### 4.2 DTO de request

**Arquivo novo:** `app/application/dtos/request/payment_link.py`

```python
from decimal import Decimal
from pydantic import BaseModel, ConfigDict
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System

class CreatePaymentLinkDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    value: Decimal
    billing_type: PaymentType          # UNDEFINED recomendado
    description: str
    due_date_limit_days: int = 3
    system: System
    system_payment_id: str             # ID interno do pedido/pacote
    webhook_link: str                  # URL do marketfy para receber status
```

### 4.3 Use case

**Arquivo novo:** `app/application/use_cases/create_payment_link.py`

```python
class CreatePaymentLink:
    async def execute(
        self,
        request: CreatePaymentLinkDTO,
        gateway_provider: GatewayProvider,
    ) -> CreatePaymentLinkResponse:
        # 1. Verificar idempotência via system_payment_id
        existing = await self.payment_repo.get_by_system_ref(
            request.system_payment_id, request.system
        )
        if existing:
            return CreatePaymentLinkResponse(
                payment_id=existing.id,
                checkout_url=existing.checkout_link,
                payment_status=existing.payment_status,
            )

        # 2. Montar external_reference rastreável
        external_reference = f"payment:{request.system.value}:{request.system_payment_id}"

        # 3. Chamar Asaas
        gateway = self.get_gateway.get(gateway=gateway_provider)
        link_info = await gateway.create_payment_link(
            name=request.description,
            value=request.value,
            billing_type=request.billing_type,
            description=request.description,
            external_reference=external_reference,
            due_date_limit_days=request.due_date_limit_days,
        )

        # 4. Persistir Payment com checkout_link = link_info.checkout_url
        #    provider_payment_id = link_info.payment_link_id (atualizado p/ pay_xxx via webhook)
        payment = Payment.create_standalone_payment(
            ...,
            provider_payment_id=link_info.payment_link_id,
            checkout_link=link_info.checkout_url,
            webhook_link=request.webhook_link,
        )
        payment.payment_status = PaymentStatus.PENDING
        payment = await self.payment_repo.save(payment)

        return CreatePaymentLinkResponse(
            payment_id=payment.id,
            checkout_url=link_info.checkout_url,
            payment_status=payment.payment_status,
        )
```

### 4.4 Worker

**Arquivo:** `app/workers/tasks.py`

Adicionar função `create_payment_link_worker`:

```python
async def create_payment_link_worker(ctx, dto_dict: dict, system_str: str):
    # Semelhante ao create_payment_worker, mas usa CreatePaymentLink use case.
    # Não recebe customer_provider_id.
    # Retorna { "checkout_url": "...", "payment_id": "..." } no resultado do job.
```

### 4.5 Endpoint

**Arquivo:** `app/web/routes/payment_links.py` (novo)

```
POST /v1/payment-links
Headers: X-System, X-API-Key, Idempotency-Key
Scope: payments:create
```

**Request:**
```json
{
  "value": "72.00",
  "billing_type": "UNDEFINED",
  "description": "Créditos NF-e — pack_100",
  "due_date_limit_days": 3,
  "system": "marketfy",
  "system_payment_id": "550e8400-e29b-41d4-a716-446655440000",
  "webhook_link": "https://api-marketfy.neectify.com/api/v1/webhooks/billing-core"
}
```

**Response `202`:**
```json
{
  "job_id": "arq:job:...",
  "message": "Checkout enviado para criação."
}
```

**GET /v1/jobs/{job_id}** quando `status = completed`:
```json
{
  "job_id": "...",
  "status": "completed",
  "result": {
    "payment_id": "3fa85f64-...",
    "checkout_url": "https://www.asaas.com/c/pml_000005219613"
  }
}
```

### 4.6 Webhook processing — ajuste no lookup

**Arquivo:** `app/application/use_cases/process_webhook.py`

O webhook do Asaas para uma cobrança gerada por payment link chega com `payment.id = pay_xxx` (charge ID), mas o payment record local foi criado com `provider_payment_id = pml_xxx` (link ID).

O `ProcessWebhookService` precisará de um fallback:
```
1. Tentar encontrar payment por details.id (charge ID) → não encontra
2. Tentar encontrar payment por externalReference → encontra
3. Atualizar provider_payment_id para o charge ID real
4. Atualizar payment_status
```

---

## 5. Mudanças no schema de `INTERNAL_API_CLIENTS`

O escopo `customers:create` **não é mais necessário** para o fluxo de créditos fiscais do Marketfy quando usando payment links.

Escopos mínimos necessários para Marketfy após a refatoração:
```json
{
  "marketfy": {
    "scopes": [
      "payments:create",
      "payments:read",
      "subscriptions:create",
      "subscriptions:cancel",
      "jobs:read"
    ]
  }
}
```

> `customers:create` pode ser mantido para compatibilidade com fluxos que ainda precisam de customer (ex: assinaturas), mas não é mais requerido para checkout.

---

## 6. Compatibilidade com fluxo atual

| Fluxo | Endpoint atual | Situação |
|---|---|---|
| Checkout de créditos fiscais (Marketfy) | `POST /v1/payments` | **Migrar para** `POST /v1/payment-links` |
| Assinatura de planos (Marketfy) | `POST /v1/subscriptions` | **Mantém** — assinatura recorrente ainda precisa de customer |
| Outros produtos (Shop, Food) | `POST /v1/payments` | Sem mudança — decision por produto |

> O endpoint `POST /v1/payments` **não deve ser removido**. Apenas o Marketfy migrará para payment links no fluxo de créditos.

---

## 7. Checklist de implementação

```
BILLING CORE
[ ] AsaasProvider.create_payment_link() + CreatePaymentLinkGatewayResponse
[ ] InterfaceGateway.create_payment_link() (método abstrato)
[ ] CreatePaymentLinkDTO (request DTO)
[ ] CreatePaymentLinkResponse (response DTO)
[ ] CreatePaymentLink (use case)
[ ] create_payment_link_worker (tasks.py)
[ ] POST /v1/payment-links (route + schema)
[ ] Registrar router em main.py
[ ] ProcessWebhookService: fallback lookup por externalReference
[ ] Testes unitários: use case + worker
[ ] Testes de contrato: POST /v1/payment-links

AMBIENTE
[ ] Não são necessárias mudanças de .env no billing_core para este fluxo
[ ] Em sandbox: testar com POST /v3/paymentLinks na API sandbox do Asaas
```

---

## 8. Referência Asaas

- `POST /v3/paymentLinks` → cria link de pagamento
- `chargeType: DETACHED` → uma cobrança avulsa por acesso
- `billingType: UNDEFINED` → comprador escolhe PIX, boleto ou cartão no checkout
- `url` na resposta → URL do checkout que o comprador deve acessar
- Webhook `PAYMENT_RECEIVED` → mesmo evento que cobrança avulsa, inclui `externalReference`
- Webhook `PAYMENT_LINK_ACTIVATED` → acionado quando o link tem seu primeiro pagamento confirmado

Sources:
- [Criando um link de pagamentos](https://docs.asaas.com/docs/criando-um-link-de-pagamentos)
- [Criar um link de pagamentos — referência](https://docs.asaas.com/reference/criar-um-link-de-pagamentos)
- [Checkout link e redirecionamento](https://docs.asaas.com/docs/checkout-link-and-customer-redirection)
- [Eventos de cobrança (webhooks)](https://docs.asaas.com/docs/payment-events)
