# Asaas Checkout Migration Plan

**Data:** 2026-05-27  
**Objetivo:** Substituir o fluxo `payment-links` pelo Asaas Checkout nativo para compras avulsas de créditos/pacotes Marketfy. O endpoint `POST /v1/payment-links` será completamente removido — sem wrapper, sem deprecation header, sem fallback oculto.

**Stack:** FastAPI · ARQ · Redis · SQLAlchemy async · Alembic · Pydantic v2 · Asaas API v3

---

## 1. Referências da Documentação Oficial

Toda decisão de implementação abaixo é rastreável a uma das fontes:

| Fonte | O que define |
|---|---|
| [Asaas Checkout — visão geral](https://docs.asaas.com/docs/checkout-asaas) | Checkout é um formulário de pagamento pronto para fluxos digitais. Suporta múltiplos meios, expiração, redirecionamento pós-venda, split e dados do cliente. |
| [POST /v3/checkouts — referência](https://docs.asaas.com/reference/criar-novo-checkout) | Endpoint oficial; campos obrigatórios: `billingTypes`, `chargeTypes`, `callback`, `items`; `minutesToExpire` aceita 10–1440; `externalReference` máx. 200 chars. |
| [Link e redirecionamento](https://docs.asaas.com/docs/link-do-checkout-e-redirecionamento-do-cliente) | A URL pública do checkout é construída localmente: `https://asaas.com/checkoutSession/show?id={checkout_id}`. O campo `link` na resposta não é garantido. |
| [Checkout events](https://docs.asaas.com/docs/checkout-events) | Eventos de webhook específicos: `CHECKOUT_CREATED`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED`, `CHECKOUT_PAID`. |
| [Payment Links — visão geral](https://docs.asaas.com/docs/payment-links-overview) | Links de pagamento são primitivos compartilháveis sem controle de expiração, sem itemização e sem redirecionamento estruturado. Não adequados para fluxo de compra profissional. |
| [POST /v3/payments — referência](https://docs.asaas.com/reference/criar-nova-cobranca) | Cobrança vinculada a cliente: exige `customer`, suporta `externalReference`. Mantido para fluxos legados e assinaturas. |

### Por que Checkout em vez de Payment Link

O `AsaasProvider.create_payment_link` atual (`app/infra/interfaces/asaas_provider.py:200`) chama `POST /paymentLinks` e devolve `response["url"]` — um campo não garantido pela documentação. O Checkout:
- Tem `minutesToExpire` (Payment Links não expiram);
- Tem `items` com quantidade e valor por linha;
- Tem `callback.successUrl / cancelUrl / expiredUrl` por transação;
- Tem eventos de webhook próprios (`CHECKOUT_PAID` etc.);
- Tem URL de construção documentada e estável.

---

## 2. Escopo

### Incluso

- Novo endpoint `POST /v1/checkouts` — fluxo async (202 + job polling), igual ao padrão atual.
- Criar Asaas Checkout com `billingTypes: [PIX, CREDIT_CARD]`, `chargeTypes: [DETACHED]`.
- Persistir checkout como `Payment` local com `provider_payment_id = checkout_id`.
- Processar eventos `CHECKOUT_*` via webhook existente.
- Remover **completamente** toda implementação de `payment-links`.

### Excluído

- Checkout para assinaturas.
- Parcelamento via Checkout.
- Split de pagamento.
- Card entry transparente (Billing Core nunca toca em dados de cartão).
- Novo modelo de banco de dados — `Payment` existente é suficiente para v1.

---

## 3. Decisões de Arquitetura

### 3.1 Endpoint Público

```
POST /v1/checkouts
Headers: X-System, X-API-Key, Idempotency-Key
Scope: payments:create
Response: 202 { "job_id": "...", "message": "..." }
```

O endpoint segue o mesmo padrão de `POST /v1/payment-links` ([`app/web/routes/payment_links.py`](../app/web/routes/payment_links.py)) — enfileira worker, retorna job_id, resultado via `GET /v1/jobs/{job_id}`. Não há breaking change para o consumidor que já usa job polling.

### 3.2 Request Schema

```python
# app/web/schemas/checkout.py
class CreateCheckoutRequest(BaseModel):
    system: System
    system_payment_id: str
    description: str
    value: Decimal
    minutes_to_expire: int = Field(default=30, ge=10, le=1440)
    items: list[CheckoutItem]         # mínimo 1
    success_url: AnyHttpUrl
    cancel_url: AnyHttpUrl
    expired_url: AnyHttpUrl
    webhook_link: str

class CheckoutItem(BaseModel):
    name: str
    description: str = ""
    quantity: int = Field(ge=1)
    value: Decimal
```

**Validações obrigatórias:**
- `system` deve coincidir com `X-System` do header (igual ao padrão atual em `payment_links.py`).
- `value == sum(item.quantity * item.value for item in items)` — divergência retorna 422.
- `success_url`, `cancel_url`, `expired_url`: scheme HTTPS + domínio no `ALLOWED_CHECKOUT_REDIRECT_HOSTS`.
- `webhook_link`: domínio no `ALLOWED_INTERNAL_WEBHOOK_HOSTS` (mesmo allowlist atual).
- `minutes_to_expire`: 10–1440 conforme [documentação oficial](https://docs.asaas.com/reference/criar-novo-checkout).
- `system_payment_id`: único por `system` (idempotência de negócio).
- `externalReference` resultante (`checkout:{system}:{system_payment_id}`) ≤ 200 chars.

### 3.3 DTO de Aplicação

```python
# app/application/dtos/request/checkout.py
class CreateCheckoutDTO(BaseModel):
    system: System
    system_payment_id: str
    description: str
    value: Decimal
    minutes_to_expire: int
    items: list[CheckoutItemDTO]
    success_url: str
    cancel_url: str
    expired_url: str
    webhook_link: str
```

```python
# app/application/dtos/response/checkout.py
class CreateCheckoutResponse(BaseModel):
    payment_id: str
    checkout_url: str
    payment_status: PaymentStatus
```

### 3.4 Interface de Gateway

Adicionar em [`app/application/interfaces/gateway_provider.py`](../app/application/interfaces/gateway_provider.py):

```python
@dataclass
class CreateCheckoutGatewayResponse:
    checkout_id: str
    checkout_url: str

# Adicionar a InterfaceGateway:
@abstractmethod
async def create_checkout(
    self,
    billing_types: list[str],
    charge_types: list[str],
    minutes_to_expire: int,
    external_reference: str,
    callback: dict,          # {"successUrl": ..., "cancelUrl": ..., "expiredUrl": ...}
    items: list[dict],
) -> CreateCheckoutGatewayResponse:
    ...
```

Remover de `InterfaceGateway`:
- `create_payment_link()` (linha 113)
- `CreatePaymentLinkGatewayResponse` (linha 44)

### 3.5 Implementação Asaas

Adicionar em [`app/infra/interfaces/asaas_provider.py`](../app/infra/interfaces/asaas_provider.py):

```python
async def create_checkout(
    self,
    billing_types: list[str],
    charge_types: list[str],
    minutes_to_expire: int,
    external_reference: str,
    callback: dict,
    items: list[dict],
) -> CreateCheckoutGatewayResponse:
    payload = {
        "billingTypes": billing_types,
        "chargeTypes": charge_types,
        "minutesToExpire": minutes_to_expire,
        "externalReference": external_reference,
        "callback": callback,
        "items": items,
    }
    response = await self.asaas.post("/checkouts", payload)
    checkout_id = response["id"]
    # URL construída localmente conforme:
    # https://docs.asaas.com/docs/link-do-checkout-e-redirecionamento-do-cliente
    checkout_url = f"https://asaas.com/checkoutSession/show?id={checkout_id}"
    return CreateCheckoutGatewayResponse(checkout_id=checkout_id, checkout_url=checkout_url)
```

Remover de `AsaasProvider`:
- `create_payment_link()` (linha 200–222)

### 3.6 Persistência Local

Reusa `Payment` existente — sem nova tabela:

```python
Payment.create_standalone_payment(
    description=dto.description,
    gateway=gateway_provider,
    system_payment_id=dto.system_payment_id,
    provider_payment_id=checkout_id,          # checkout_id retornado pelo Asaas
    value=dto.value,
    from_system=dto.system,
    checkout_link=checkout_url,               # URL construída localmente
    webhook_link=dto.webhook_link,
    due_date=None,
    external_reference=f"checkout:{dto.system.value}:{dto.system_payment_id}",
)
payment.payment_status = PaymentStatus.PENDING
payment.payment_type = PaymentType.UNDEFINED   # múltiplos meios — definido no momento do pagamento
```

### 3.7 Idempotência e Rastreabilidade de Gateway

Novos namespaces — nunca reutilizar os do payment-link:

| Item | Valor |
|---|---|
| Namespace Redis (API) | `checkout_create` |
| `operation_name` (GatewayOperation) | `create_checkout` |
| `dedupe_key` | `create_checkout:{system}:{system_payment_id}` |
| `external_reference` | `checkout:{system}:{system_payment_id}` |

A lógica de `GatewayOperation` (estados `FAILED` → retry, `COMPLETED` → conflict, `REQUIRES_RECONCILIATION` → bloqueia) é idêntica à de [`app/application/use_cases/create_payment_link.py`](../app/application/use_cases/create_payment_link.py) e deve ser replicada em `CreateCheckout.execute`.

---

## 4. Tratamento de Webhooks

### 4.1 Eventos Oficiais

Conforme [Checkout events](https://docs.asaas.com/docs/checkout-events), os eventos enviados pelo Asaas são:

| Evento | Payload principal | Ação esperada |
|---|---|---|
| `CHECKOUT_CREATED` | `checkout.id`, `checkout.status` | Registrar; não notificar produto |
| `CHECKOUT_PAID` | `checkout.id`, `checkout.status = PAID` | Marcar `Payment` como PAID; notificar produto |
| `CHECKOUT_CANCELED` | `checkout.id`, `checkout.status = CANCELED` | Marcar `Payment` como CANCELED; notificar produto |
| `CHECKOUT_EXPIRED` | `checkout.id`, `checkout.status = EXPIRED` | Marcar `Payment` como EXPIRED; notificar produto |

### 4.2 Extensão de `EventType`

Em [`app/application/dtos/request/webhook.py`](../app/application/dtos/request/webhook.py), adicionar ao enum `EventType`:

```python
CHECKOUT_CREATED = "CHECKOUT_CREATED"
CHECKOUT_PAID = "CHECKOUT_PAID"
CHECKOUT_CANCELED = "CHECKOUT_CANCELED"
CHECKOUT_EXPIRED = "CHECKOUT_EXPIRED"
```

O `field_validator("event")` existente já aceita valores desconhecidos — os novos valores apenas precisam ser declarados.

### 4.3 Normalização no `AsaasProvider`

O `normalize_webhook` atual ([`app/infra/interfaces/asaas_provider.py:96`](../app/infra/interfaces/asaas_provider.py)) trata `payment` e `subscription`. Adicionar branch para `checkout`:

```python
def normalize_webhook(self, payload: dict) -> WebhookPayload:
    if "details" in payload:
        return WebhookPayload.model_validate(payload)

    event = payload.get("event")
    source_event_id = payload.get("id")

    # --- branch existente: payment / subscription ---
    payment = payload.get("payment") or {}
    subscription = payload.get("subscription") or {}

    # --- branch novo: checkout ---
    checkout = payload.get("checkout") or {}
    if checkout:
        items = checkout.get("items") or []
        total = sum(
            Decimal(str(i.get("value", 0))) * int(i.get("quantity", 1))
            for i in items
        )
        normalized = {
            "event": event,
            "source_event_id": source_event_id,
            "details": {
                "id": checkout.get("id"),
                "subscription": None,
                "status": checkout.get("status"),
                "value": checkout.get("value") or (total if total else None),
                "net_value": checkout.get("netValue"),
                "payment_date": checkout.get("paymentDate") or checkout.get("dateCreated"),
                "external_reference": checkout.get("externalReference"),
            },
        }
        return WebhookPayload.model_validate(normalized)

    # branch payment/subscription (existente, sem alteração)
    normalized = {
        "event": event,
        "source_event_id": source_event_id,
        "details": {
            "id": payment.get("id") or payload.get("paymentId"),
            "subscription": payment.get("subscription") or subscription.get("id"),
            "status": payment.get("status") or subscription.get("status"),
            "value": payment.get("value"),
            "net_value": payment.get("netValue"),
            "payment_date": payment.get("paymentDate"),
            "external_reference": payment.get("externalReference"),
        },
    }
    return WebhookPayload.model_validate(normalized)
```

### 4.4 Processamento em `ProcessWebhookService`

Em [`app/application/use_cases/process_webhook.py`](../app/application/use_cases/process_webhook.py), adicionar tratamento antes do bloco `PAYMENT_RECEIVED`:

```python
# Eventos de Checkout — lookup por provider_id (checkout.id) com fallback em external_reference
if payload.event in (
    EventType.CHECKOUT_CREATED,
    EventType.CHECKOUT_PAID,
    EventType.CHECKOUT_CANCELED,
    EventType.CHECKOUT_EXPIRED,
):
    if payload.event == EventType.CHECKOUT_CREATED:
        # Apenas registra — checkout já persistido pelo worker de criação
        event.mark_as_processed()
        await self.webhook_event_repo.save(event)
        await self.uow.commit()
        return None

    payment = await self.payment_repo.get_by_provider_id(payload.details.id)
    if payment is None and payload.details.external_reference:
        payment = await self.payment_repo.get_by_external_reference(
            payload.details.external_reference
        )

    if payment is None:
        logger.warning(
            "Checkout payment nao encontrado",
            extra={"event_id": event_id, "checkout_id": payload.details.id},
        )
        event.mark_as_processed()
        await self.webhook_event_repo.save(event)
        await self.uow.commit()
        return None

    status_map = {
        EventType.CHECKOUT_PAID: "CONFIRMED",
        EventType.CHECKOUT_CANCELED: "CANCELED",
        EventType.CHECKOUT_EXPIRED: "EXPIRED",  # se PaymentStatus.EXPIRED não existir, mapear para CANCELED
    }
    changed = apply_gateway_payment_status(
        payment,
        status_map[payload.event],
        payload.details.payment_date.date() if payload.details.payment_date else None,
        payload.details.net_value,
    )
    if changed:
        payment = await self.payment_repo.save(payment)

    event.mark_as_processed()
    await self.webhook_event_repo.save(event)
    await self.uow.commit()

    if not changed:
        return None

    return ProcessWebhookResponse(
        event=InternalEventType.PAYMENT_STATUS_UPDATED,
        payment_id=payment.id,
        subscription_id=None,
    )
```

### 4.5 Idempotência de Eventos

O mecanismo atual de `event_id` em `WebhookPayload.event_id_for()` ([`webhook.py:68`](../app/application/dtos/request/webhook.py)) já funciona para checkout:

```
event_id = "asaas:CHECKOUT_PAID:{checkout_id}:no-subscription"
```

Eventos duplicados (Asaas pode reenviar em falha de entrega) são idempotentes por `webhook_events.event_id`.

---

## 5. Segurança

### Secrets e Credenciais
- Rotacionar `ASAAS_API_TOKEN` e `ASAAS_WEBHOOK_SECRET` antes do rollout em produção.
- Nunca logar headers `access_token`, body completo ou URL com tokens.
- Manter comparação em tempo constante para `asaas-access-token` (padrão atual).

### Validação de Entrada
- Adicionar `ALLOWED_CHECKOUT_REDIRECT_HOSTS` ao [`app/infra/config.py`](../app/infra/config.py) como `list[str]`.
- Validar scheme HTTPS + domínio nos campos `success_url`, `cancel_url`, `expired_url`.
- Manter validação de `webhook_link` contra `ALLOWED_INTERNAL_WEBHOOK_HOSTS` (padrão atual).
- Não receber dados de cartão no Billing Core em nenhum momento.
- `externalReference` nunca deve conter CPF, e-mail, token ou qualquer dado sensível — apenas `checkout:{system}:{id}`.

### Expiração e Stale Checkouts
- `minutes_to_expire` obrigatório (default 30, max 1440) reduz risco de checkout aberto indefinidamente.
- Asaas enviará `CHECKOUT_EXPIRED` automaticamente — sem necessidade de TTL manual.

### Replay Protection
- O replay protection atual é aplicado na camada HTTP antes do enqueue.
- Evento só é marcado como processado após commit bem-sucedido no worker.
- Para falhas parciais (gateway criou, save local falhou): `GatewayOperation.REQUIRES_RECONCILIATION` bloqueia nova tentativa e exige intervenção operacional.
- Adicionar script operacional para limpar replay keys por `event_id` em caso de dead-letter.

---

## 6. Arquivos

### Criar

| Arquivo | Descrição |
|---|---|
| `app/application/dtos/request/checkout.py` | `CreateCheckoutDTO`, `CheckoutItemDTO` |
| `app/application/dtos/response/checkout.py` | `CreateCheckoutResponse` |
| `app/application/use_cases/create_checkout.py` | `CreateCheckout.execute` |
| `app/web/schemas/checkout.py` | `CreateCheckoutRequest`, `CheckoutItem` com validators |
| `app/web/routes/checkouts.py` | `POST /v1/checkouts` |
| `tests/test_create_checkout_use_case.py` | Use case unit tests |
| `tests/test_checkout_workers.py` | Worker integration tests |
| `tests/test_asaas_checkout_provider.py` | Provider unit tests |
| `tests/test_checkout_webhook_normalization.py` | Normalization unit tests |

### Modificar

| Arquivo | O que muda |
|---|---|
| `app/application/interfaces/gateway_provider.py` | Adiciona `CreateCheckoutGatewayResponse`, `create_checkout`; remove `CreatePaymentLinkGatewayResponse`, `create_payment_link` |
| `app/infra/interfaces/asaas_provider.py` | Adiciona `create_checkout`; remove `create_payment_link`; estende `normalize_webhook` |
| `app/application/dtos/request/webhook.py` | Adiciona 4 novos `EventType` |
| `app/application/use_cases/process_webhook.py` | Adiciona branch para `CHECKOUT_*` |
| `app/workers/tasks.py` | Adiciona `create_checkout_worker`; remove `create_payment_link_worker` |
| `app/workers/worker.py` | Registra `create_checkout_worker`; remove `create_payment_link_worker` |
| `app/web/main.py` | Inclui `checkouts_router`; remove `payment_links_router` |
| `app/infra/config.py` | Adiciona `ALLOWED_CHECKOUT_REDIRECT_HOSTS` |
| `docs/API.md` | Substitui documentação de `POST /v1/payment-links` por `POST /v1/checkouts` |
| `docs/INTEGRATION.md` | Atualiza fluxo de compra avulsa |
| `docs/Onboarding_SaaS.md` | Remove referências a payment-links |
| `docs/Fluxos.md` | Atualiza diagrama de fluxo de compra |
| `docs/Ambiente.md` | Adiciona `ALLOWED_CHECKOUT_REDIRECT_HOSTS` à lista de variáveis |

### Deletar

| Arquivo |
|---|
| `app/application/dtos/request/payment_link.py` |
| `app/application/dtos/response/payment_link.py` |
| `app/application/use_cases/create_payment_link.py` |
| `app/web/schemas/payment_link.py` |
| `app/web/routes/payment_links.py` |
| `tests/test_create_payment_link_use_case.py` |

### Remover de Arquivos Existentes

- `CreatePaymentLinkGatewayResponse` e `InterfaceGateway.create_payment_link` de `gateway_provider.py`.
- `AsaasProvider.create_payment_link` de `asaas_provider.py`.
- Testes de payment-link em `tests/test_api_contracts.py` e `tests/test_payment_workers.py`.
- `create_payment_link_worker` de `tasks.py` e registro em `worker.py`.
- `payment_links_router` e `app.include_router(payment_links_router)` de `main.py`.

---

## 7. Roadmap de Implementação

### Task 1 — DTOs e Schemas de Checkout

**Arquivos:** `app/application/dtos/request/checkout.py`, `app/application/dtos/response/checkout.py`, `app/web/schemas/checkout.py`, `tests/test_api_contracts.py`

- [ ] Escrever testes de contrato falhando:
  - `Idempotency-Key` ausente → 422
  - `X-System` diferente de `payload.system` → 403
  - `success_url` com domínio fora de `ALLOWED_CHECKOUT_REDIRECT_HOSTS` → 422
  - `minutes_to_expire = 9` → 422
  - `minutes_to_expire = 1441` → 422
  - `value != sum(items)` → 422
  - Request válida → 202 com `job_id`
  - Request duplicada (mesmo `Idempotency-Key`) → 202 com mesmo `job_id`
- [ ] Implementar `CheckoutItem` e `CreateCheckoutRequest` com validators Pydantic.
- [ ] Implementar `CreateCheckoutDTO`, `CheckoutItemDTO`.
- [ ] Implementar `CreateCheckoutResponse`.
- [ ] `python -m pytest tests/test_api_contracts.py -k checkout`

### Task 2 — Interface de Gateway e Provider Asaas

**Arquivos:** `app/application/interfaces/gateway_provider.py`, `app/infra/interfaces/asaas_provider.py`, `tests/test_asaas_checkout_provider.py`

- [ ] Escrever testes falhando:
  - Payload enviado a `/checkouts` contém `billingTypes`, `chargeTypes`, `minutesToExpire`, `externalReference`, `callback`, `items`.
  - `checkout_url` é construída localmente como `https://asaas.com/checkoutSession/show?id={id}`, não lida do response.
  - `AsaasAPIError` é propagada corretamente (mock retorna 4xx).
- [ ] Adicionar `CreateCheckoutGatewayResponse` e `InterfaceGateway.create_checkout`.
- [ ] Implementar `AsaasProvider.create_checkout`.
- [ ] Remover `CreatePaymentLinkGatewayResponse`, `create_payment_link` da interface e do provider.
- [ ] `python -m pytest tests/test_asaas_checkout_provider.py`

### Task 3 — Use Case `CreateCheckout`

**Arquivos:** `app/application/use_cases/create_checkout.py`, `tests/test_create_checkout_use_case.py`

- [ ] Escrever testes falhando:
  - Checkout criado com sucesso: `Payment` persistido com `provider_payment_id = checkout_id` e `checkout_link = checkout_url`.
  - `system_payment_id` duplicado: retorna `checkout_url` do `Payment` existente sem chamar o gateway.
  - Gateway cria checkout, save local falha: `GatewayOperation` marcada como `REQUIRES_RECONCILIATION`.
  - `GatewayOperation` com status `COMPLETED` e sem `Payment` espelho: levanta `DomainError`.
- [ ] Implementar `CreateCheckout.execute` seguindo o mesmo contrato de `CreatePaymentLink.execute` ([`create_payment_link.py`](../app/application/use_cases/create_payment_link.py)).
- [ ] `external_reference = f"checkout:{dto.system.value}:{dto.system_payment_id}"`.
- [ ] `operation_name = "create_checkout"`, `dedupe_key = f"create_checkout:{system}:{system_payment_id}"`.
- [ ] `python -m pytest tests/test_create_checkout_use_case.py`

### Task 4 — Worker e Rota

**Arquivos:** `app/workers/tasks.py`, `app/workers/worker.py`, `app/web/routes/checkouts.py`, `app/web/main.py`, `tests/test_checkout_workers.py`

- [ ] Escrever testes falhando:
  - `create_checkout_worker` retorna resultado público com `checkout_url`.
  - `GET /v1/jobs/{job_id}` expõe `result.checkout_url` após worker concluir.
  - `POST /v1/checkouts` idempotente: segunda chamada com mesmo `Idempotency-Key` retorna mesmo `job_id`.
- [ ] Implementar `create_checkout_worker` em `tasks.py`.
- [ ] Registrar em `worker.py` (função nomeada `"workers:tasks.create_checkout_worker"`).
- [ ] Implementar `POST /v1/checkouts` em `checkouts.py`.
- [ ] Registrar `checkouts_router` em `main.py`.
- [ ] `python -m pytest tests/test_checkout_workers.py tests/test_api_contracts.py -k checkout`

### Task 5 — Webhook: Normalização e Processamento

**Arquivos:** `app/application/dtos/request/webhook.py`, `app/infra/interfaces/asaas_provider.py`, `app/application/use_cases/process_webhook.py`, `tests/test_checkout_webhook_normalization.py`, `tests/test_process_webhook_use_case.py`

- [ ] Escrever testes falhando para normalização:
  - Payload com `checkout` presente → `details.id = checkout.id`, `details.status = checkout.status`.
  - `CHECKOUT_PAID` → `details.payment_date` mapeado de `checkout.paymentDate`.
  - `checkout.value` ausente → calculado de `sum(items[].quantity * items[].value)`.
  - Payload sem `checkout` → branch existente inalterado.
- [ ] Escrever testes falhando para processamento:
  - `CHECKOUT_CREATED` → retorna `None`, evento marcado como processado.
  - `CHECKOUT_PAID` → `Payment.payment_status = PAID`, retorna `PAYMENT_STATUS_UPDATED`.
  - `CHECKOUT_EXPIRED` → `Payment` marcado como expirado/cancelado, notifica produto.
  - `CHECKOUT_CANCELED` → `Payment` marcado como cancelado, notifica produto.
  - Evento duplicado (`event_id` já processado) → retorna `None`.
  - Checkout não encontrado (nem por `provider_id` nem por `external_reference`) → retorna `None`, loga warning.
- [ ] Adicionar 4 valores ao `EventType`.
- [ ] Estender `normalize_webhook`.
- [ ] Adicionar branch `CHECKOUT_*` em `ProcessWebhookService.execute`.
- [ ] `python -m pytest tests/test_checkout_webhook_normalization.py tests/test_process_webhook_use_case.py`

### Task 6 — Remoção Completa de Payment Links

**Arquivos:** todos listados em "Deletar" e "Remover de Arquivos Existentes"

- [ ] Deletar `app/application/dtos/request/payment_link.py`.
- [ ] Deletar `app/application/dtos/response/payment_link.py`.
- [ ] Deletar `app/application/use_cases/create_payment_link.py`.
- [ ] Deletar `app/web/schemas/payment_link.py`.
- [ ] Deletar `app/web/routes/payment_links.py`.
- [ ] Deletar `tests/test_create_payment_link_use_case.py`.
- [ ] Remover `CreatePaymentLinkGatewayResponse` e `create_payment_link` de `gateway_provider.py`.
- [ ] Remover `AsaasProvider.create_payment_link` de `asaas_provider.py`.
- [ ] Remover `create_payment_link_worker` de `tasks.py` e `worker.py`.
- [ ] Remover `payment_links_router` de `main.py`.
- [ ] Remover testes de payment-link de `test_api_contracts.py` e `test_payment_workers.py`.
- [ ] Verificar ausência de referências:
  ```
  rg "payment_link|payment-links|PaymentLink|create_payment_link|paymentLink" app tests docs
  ```
  Somente este arquivo de planejamento pode conter os termos acima.
- [ ] `python -m pytest` — suite completa verde.

### Task 7 — Atualização de Docs

**Arquivos:** `docs/API.md`, `docs/INTEGRATION.md`, `docs/Onboarding_SaaS.md`, `docs/Fluxos.md`, `docs/Ambiente.md`

- [ ] Documentar `POST /v1/checkouts` com exemplo de request/response.
- [ ] Documentar polling via `GET /v1/jobs/{job_id}` e campo `result.checkout_url`.
- [ ] Documentar eventos de checkout webhook e configuração no painel Asaas.
- [ ] Documentar `ALLOWED_CHECKOUT_REDIRECT_HOSTS` em `Ambiente.md`.
- [ ] Remover toda documentação de `POST /v1/payment-links`.
- [ ] Verificar: `rg "POST /v1/payment-links|payment-links" docs` — zero resultados em docs ativos.

### Task 8 — Sandbox e Rollout

- [ ] Rotacionar `ASAAS_API_TOKEN` e `ASAAS_WEBHOOK_SECRET` no ambiente alvo.
- [ ] Executar migrations.
- [ ] Deploy de API + worker juntos (um único artefato — sem janela onde worker antigo processa checkout).
- [ ] Criar checkout via `POST /v1/checkouts` no sandbox.
- [ ] Pollar `GET /v1/jobs/{job_id}` e verificar `result.checkout_url`.
- [ ] Abrir `checkout_url` no browser e confirmar formulário Asaas.
- [ ] Pagar com PIX de teste no sandbox.
- [ ] Verificar que `CHECKOUT_PAID` atualiza `Payment.payment_status = PAID` localmente.
- [ ] Verificar que Marketfy recebe `PAYMENT_STATUS_UPDATED`.
- [ ] Verificar que `POST /v1/payment-links` retorna `404`.
- [ ] Verificar que nenhum worker responde a `workers:tasks.create_payment_link_worker`.

---

## 8. Testes

### Testes Unitários

| Classe/Função | Casos |
|---|---|
| `CreateCheckoutRequest` (Pydantic) | HTTPS obrigatório em URLs de redirect; `value != sum(items)` → ValidationError; `minutes_to_expire` fora de 10–1440 → ValidationError |
| `AsaasProvider.create_checkout` | Payload correto para `/checkouts`; URL construída localmente; erro 4xx propagado como `AsaasAPIError` |
| `CreateCheckout.execute` | Criação bem-sucedida; idempotência por `system_payment_id`; reconciliação quando save local falha |
| `AsaasProvider.normalize_webhook` | Branch `checkout` → `details` corretos; `value` calculado de `items` quando ausente; branch `payment` inalterado |
| `ProcessWebhookService.execute` | `CHECKOUT_PAID` → PAID; `CHECKOUT_EXPIRED` → status; `CHECKOUT_CREATED` → None; duplicata → None |

### Testes de Contrato (API)

| Endpoint | Casos |
|---|---|
| `POST /v1/checkouts` | Auth, scope, idempotência, validações, enqueue |
| `GET /v1/jobs/{job_id}` | `result.checkout_url` presente no resultado do worker |
| `POST /v1/payment-links` | 404 após remoção |

### Testes de Worker

- `create_checkout_worker` produz resultado público com `checkout_url`.
- `process_webhook_worker` processa eventos `CHECKOUT_*` de forma idempotente.
- `send_internal_webhook` recebe `PAYMENT_STATUS_UPDATED` após `CHECKOUT_PAID`.

### Testes de Integração / Sandbox

1. Criar checkout via API → receber `job_id`.
2. Pollar até `status = completed` → `checkout_url` disponível.
3. Abrir URL → formulário Asaas renderizado.
4. Pagar com PIX de teste.
5. Asaas envia `CHECKOUT_PAID`.
6. `Payment.payment_status = PAID` confirmado localmente.
7. Marketfy recebe `PAYMENT_STATUS_UPDATED` via webhook interno.

---

## 9. Rollback

A remoção de payment-links é destrutiva — rollback requer reversão de código:

1. Antes do rollout: manter branch `pre-checkout-migration` como ponto de rollback imediato.
2. Em falha de criação de checkout: reverter deployment para última versão com payment-links.
3. `/v1/payments` (customer-bound) não é afetado e permanece como fallback para cobranças vinculadas a cliente.
4. Não manter `/v1/payment-links` como fallback oculto após go-live — ambiguidade de rota gera bugs de reconciliação.
5. Falha em webhook `CHECKOUT_PAID` após pagamento: reprocessar via dead-letter/event-id tooling após o fix, sem criar nova cobrança.

---

## 10. Critérios de Aceite

- [ ] `POST /v1/checkouts` cria Asaas Checkout e retorna `job_id`.
- [ ] `GET /v1/jobs/{job_id}` retorna `result.checkout_url`.
- [ ] `checkout_url` abre como `https://asaas.com/checkoutSession/show?id={checkout_id}`.
- [ ] `CHECKOUT_PAID` marca `Payment` como pago localmente.
- [ ] `CHECKOUT_EXPIRED` marca `Payment` como expirado/cancelado.
- [ ] `CHECKOUT_CANCELED` marca `Payment` como cancelado.
- [ ] Marketfy recebe `PAYMENT_STATUS_UPDATED` nos três casos acima.
- [ ] `POST /v1/payment-links` retorna 404.
- [ ] Nenhum worker registrado como `create_payment_link_worker`.
- [ ] `rg "payment_link|paymentLink" app tests docs` → zero resultados em código ativo.
- [ ] `python -m pytest` verde.
- [ ] Docs ativos não mencionam payment links.
