# Assinatura: back_url por requisicao e consulta de status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar `POST /v1/subscriptions` aceitar um `back_url` opcional por requisicao e adicionar `GET /v1/subscriptions/{subscription_id}` para o consumidor consultar o status ao vivo no gateway, sem mudar nenhum contrato existente.

**Architecture:** `back_url` e um campo novo e opcional em `CreateSubscriptionDTO`/`CreateSubscriptionRequest`, passado pelo use case ate `InterfaceGateway.create_subscription()` como kwarg opcional (`AsaasProvider` ignora, `MercadoPagoProvider` usa se vier, senao cai no `settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL` de hoje). O endpoint de status novo nao precisa de metodo de adapter novo: `InterfaceGateway.verify_status()` ja existe nos dois adapters e ja devolve o vocabulario compartilhado (`ACTIVE`/`PENDING`/`PAUSED`/`CANCELED`).

**Tech Stack:** Python 3.13, FastAPI 0.115, SQLAlchemy 2 async, pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-09-15-subscription-checkout-support-design.md`

## Global Constraints

- Nenhuma rota, payload ou header existente muda de contrato.
- `POST /v1/webhooks/asaas` e `POST /v1/webhooks/mercadopago` continuam exatamente como estao.
- Commits convencionais, terminando com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Suite completa verde ao fim de cada task: `python -m pytest -q` (ative o venv: `.venv/Scripts/python -m pytest -q` no Git Bash).
- Mensagens de erro e logs em portugues sem acento.

---

### Task 1: `back_url` opcional no DTO e no schema HTTP

**Files:**
- Modify: `app/application/dtos/request/subscription.py`
- Modify: `app/web/schemas/subscription.py`
- Test: `tests/test_subscription_request_schema.py`

**Interfaces:**
- Produces: `CreateSubscriptionDTO.back_url: str | None` e `CreateSubscriptionRequest.back_url: str | None`, ambos validados contra `settings.effective_checkout_redirect_hosts` quando presentes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_request_schema.py
import pytest
from pydantic import ValidationError

from app.web.schemas.subscription import CreateSubscriptionRequest

BASE = {
    "customer_provider_id": "cus_123",
    "value": "129.90",
    "subscription_type": "MONTHLY",
    "description": "Plano Pro",
    "system": "marketfy",
    "system_sub_id": "sub_local_1",
    "expires_at": "2099-01-01T00:00:00Z",
    "webhook_link": "https://api-marketfy.neectify.com/billing/webhooks/internal",
}


def test_back_url_is_optional():
    req = CreateSubscriptionRequest.model_validate(BASE)
    assert req.back_url is None


def test_back_url_accepts_allowed_host():
    payload = {**BASE, "back_url": "https://app.marketfy.com/billing/retorno?ref=abc"}
    req = CreateSubscriptionRequest.model_validate(payload)
    assert req.back_url == "https://app.marketfy.com/billing/retorno?ref=abc"


def test_back_url_rejects_disallowed_host():
    payload = {**BASE, "back_url": "https://evil.example/steal"}
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(payload)


def test_back_url_rejects_non_https():
    payload = {**BASE, "back_url": "http://app.marketfy.com/billing/retorno"}
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(payload)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_subscription_request_schema.py -v`
Expected: FAIL — `CreateSubscriptionRequest` nao tem o campo `back_url` (`ValidationError`/`AttributeError`).

- [ ] **Step 3: Add `back_url` ao DTO**

Em `app/application/dtos/request/subscription.py`, adicionar ao final da classe `CreateSubscriptionDTO`:

```python
    back_url: str | None = None
```

- [ ] **Step 4: Add `back_url` ao schema HTTP com validacao**

Em `app/web/schemas/subscription.py`, adicionar a `settings.effective_checkout_redirect_hosts` ja usada por `CreatePaymentRequest` (`app/web/schemas/payment.py`). Apos o campo `webhook_link`, adicionar:

```python
    back_url: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "URL de retorno apos a autorizacao no Mercado Pago. Quando ausente, usa "
            "MERCADOPAGO_SUBSCRIPTION_BACK_URL. Ignorado pelo Asaas. Precisa usar HTTPS e "
            "host permitido em ALLOWED_CHECKOUT_REDIRECT_HOSTS."
        ),
        examples=["https://app.neectify.local/billing/retorno?ref=sub-001"],
    )
```

E o validator (mesmo padrao de `CreatePaymentRequest.validate_checkout_redirect_url` em `app/web/schemas/payment.py`):

```python
    @field_validator("back_url")
    @classmethod
    def validate_back_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("back_url deve usar HTTPS.")

        allowed_hosts = settings.effective_checkout_redirect_hosts
        hostname = parsed.hostname.lower()
        if not any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in allowed_hosts):
            raise ValueError("Host do back_url nao permitido.")

        return value
```

`urlparse` e `field_validator` ja estao importados no topo do arquivo; confirme e adicione se faltar.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_subscription_request_schema.py -v`
Expected: PASS (4 testes).

- [ ] **Step 6: Run full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: todos os testes passam (nenhuma regressao — `to_worker_payload()` de `CreateSubscriptionRequest` usa `model_dump(mode="json")`, entao o campo novo, quando `None`, so acrescenta `"back_url": null` ao payload do worker; a Task 2 consome isso).

```bash
git add app/application/dtos/request/subscription.py app/web/schemas/subscription.py tests/test_subscription_request_schema.py
git commit -m "feat(subscriptions): accept optional back_url validated against the checkout redirect allowlist"
```

---

### Task 2: `back_url` chega ao adapter do Mercado Pago

**Files:**
- Modify: `app/application/interfaces/gateway_provider.py` (assinatura abstrata de `create_subscription`)
- Modify: `app/infra/interfaces/asaas_provider.py:create_subscription` (aceitar e ignorar)
- Modify: `app/infra/interfaces/mercadopago_provider.py:create_subscription` (usar quando presente)
- Modify: `app/application/use_cases/create_subscription.py` (repassar `request.back_url`)
- Test: `tests/test_mercadopago_provider_subscription.py` (arquivo ja existe — adicionar caso)

**Interfaces:**
- Consumes: `CreateSubscriptionDTO.back_url` (Task 1).
- Produces: `InterfaceGateway.create_subscription(..., back_url: str | None = None)` — assinatura nova, compativel com as chamadas existentes (parametro opcional no fim).

- [ ] **Step 1: Write the failing test**

Adicionar a `tests/test_mercadopago_provider_subscription.py` (mantendo os fixtures/fakes ja existentes no arquivo — reaproveitar o `FakeMercadoPagoAPI` e o `provider` de teste ja la; se o arquivo usa outro nome de fixture, usar o mesmo):

```python
@pytest.mark.asyncio
async def test_create_subscription_uses_request_back_url_when_present(mercadopago_provider, fake_api):
    fake_api.set_response("POST", "/preapproval", {"id": "preapproval_1"})

    await mercadopago_provider.create_subscription(
        customer_provider_id="cus_1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.MONTHLY,
        description="Plano Pro",
        external_reference="sub-local-1",
        back_url="https://app.marketfy.com/billing/retorno?ref=sub-local-1",
    )

    sent_payload = fake_api.last_request("POST", "/preapproval")["payload"]
    assert sent_payload["back_url"] == "https://app.marketfy.com/billing/retorno?ref=sub-local-1"


@pytest.mark.asyncio
async def test_create_subscription_falls_back_to_settings_back_url(mercadopago_provider, fake_api, monkeypatch):
    from app.infra.config import settings
    monkeypatch.setattr(settings, "MERCADOPAGO_SUBSCRIPTION_BACK_URL", "https://global.local/retorno")
    fake_api.set_response("POST", "/preapproval", {"id": "preapproval_2"})

    await mercadopago_provider.create_subscription(
        customer_provider_id="cus_1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.MONTHLY,
        description="Plano Pro",
        external_reference="sub-local-2",
    )

    sent_payload = fake_api.last_request("POST", "/preapproval")["payload"]
    assert sent_payload["back_url"] == "https://global.local/retorno"
```

Se o arquivo de teste existente nao tiver um helper `last_request`/`set_response` com essa assinatura exata, usar o padrao ja presente no arquivo (o spike da Task 0 do plano de 2026-09-14 ja criou um fake para `/preapproval`) — o importante e o assert final sobre `back_url` no payload enviado.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_mercadopago_provider_subscription.py -v -k back_url`
Expected: FAIL — `create_subscription()` nao aceita `back_url` (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Adicionar o parametro na interface abstrata**

Em `app/application/interfaces/gateway_provider.py`, no metodo abstrato `create_subscription`:

```python
    @abstractmethod
    async def create_subscription(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        next_due_date: date,
        cycle: SubscriptionType,
        description: str,
        external_reference: str | None = None,
        back_url: str | None = None,
    ) -> str:
        pass
```

- [ ] **Step 4: Aceitar e ignorar no Asaas**

Em `app/infra/interfaces/asaas_provider.py`, `AsaasProvider.create_subscription`: adicionar `back_url: str | None = None,` como ultimo parametro da assinatura (Asaas nao usa; nenhuma outra mudanca no corpo do metodo).

- [ ] **Step 5: Usar no Mercado Pago**

Em `app/infra/interfaces/mercadopago_provider.py`, `MercadoPagoProvider.create_subscription`: adicionar `back_url: str | None = None,` a assinatura, e trocar a linha do payload:

```python
            "back_url": settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL,
```

por:

```python
            "back_url": back_url or settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL,
```

- [ ] **Step 6: Repassar do use case**

Em `app/application/use_cases/create_subscription.py`, `CreateSubscription.execute`, no bloco `gateway_subscription_id = await gateway.create_subscription(...)`, adicionar a ultima keyword:

```python
                external_reference=request.system_sub_id,
                back_url=request.back_url,
            )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_mercadopago_provider_subscription.py -v`
Expected: PASS.

- [ ] **Step 8: Run full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: todos os testes passam (o novo parametro e opcional em toda a cadeia; nenhuma chamada existente quebra).

```bash
git add app/application/interfaces/gateway_provider.py app/infra/interfaces/asaas_provider.py app/infra/interfaces/mercadopago_provider.py app/application/use_cases/create_subscription.py tests/test_mercadopago_provider_subscription.py
git commit -m "feat(subscriptions): thread the optional back_url through to the Mercado Pago preapproval"
```

---

### Task 3: `GET /v1/subscriptions/{subscription_id}`

**Files:**
- Create: `app/application/dtos/response/subscription_status.py`
- Modify: `app/web/routes/subscriptions.py`
- Test: `tests/test_get_subscription_status_endpoint.py`

**Interfaces:**
- Consumes: `SubscriptionRepositoryINFRA.get_by_id` (ja existe, usado em `cancel_subscription`), `Subscription.belongs_to_system` (ja existe), `GetGatewayInfra().get(gateway)` (ja existe), `InterfaceGateway.verify_status(subscription_id) -> SubscriptionStatusResponse` (ja existe nos dois adapters, sem mudanca).
- Produces: `GetSubscriptionStatusResponse{subscription_id: UUID, gateway_status: str, next_due_date: date, value: Decimal, cycle: str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_get_subscription_status_endpoint.py
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.interfaces.gateway_provider import SubscriptionStatusResponse
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.web.main import app


def _subscription(sub_id: uuid.UUID) -> Subscription:
    return Subscription(
        id=sub_id,
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-local-1",
        gateway_subscription_id="preapproval_1",
        gateway_provider=GatewayProvider.MERCADOPAGO,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=System.MARKETFY,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_subscription_status_returns_live_gateway_status(monkeypatch):
    sub_id = uuid.uuid4()
    subscription = _subscription(sub_id)

    repo = AsyncMock()
    repo.get_by_id.return_value = subscription
    monkeypatch.setattr(
        "app.web.routes.subscriptions.SubscriptionRepositoryINFRA",
        lambda db: repo,
    )

    gateway = AsyncMock()
    gateway.verify_status.return_value = SubscriptionStatusResponse(
        subscription_id="preapproval_1",
        status="ACTIVE",
        deleted=False,
        next_due_date=date(2026, 11, 1),
        value=Decimal("129.90"),
        cycle="MONTHLY",
    )
    monkeypatch.setattr(
        "app.web.routes.subscriptions.GetGatewayInfra",
        lambda: type("G", (), {"get": lambda self, gateway: gateway})(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/v1/subscriptions/{sub_id}",
            headers={"X-System": "marketfy", "X-API-Key": "test-marketfy-key"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["subscription_id"] == str(sub_id)
    assert body["gateway_status"] == "ACTIVE"
    assert body["value"] == "129.90"
    assert body["cycle"] == "MONTHLY"
    gateway.verify_status.assert_awaited_once_with("preapproval_1")


@pytest.mark.asyncio
async def test_get_subscription_status_404_for_other_system(monkeypatch):
    sub_id = uuid.uuid4()
    subscription = _subscription(sub_id)
    subscription.from_system = System.NEECTIFY_FOOD

    repo = AsyncMock()
    repo.get_by_id.return_value = subscription
    monkeypatch.setattr(
        "app.web.routes.subscriptions.SubscriptionRepositoryINFRA",
        lambda db: repo,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/v1/subscriptions/{sub_id}",
            headers={"X-System": "marketfy", "X-API-Key": "test-marketfy-key"},
        )

    assert response.status_code == 404
```

Use as credenciais de teste ja configuradas em `tests/conftest.py`/fixtures existentes para o sistema `marketfy` (mesmas usadas pelos outros testes de rota em `tests/test_*_endpoint.py` — conferir o nome exato da API key de teste la e ajustar o header acima antes de rodar).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_get_subscription_status_endpoint.py -v`
Expected: FAIL — 404 (`route not found`) ou `ImportError`.

- [ ] **Step 3: Criar o response DTO**

```python
# app/application/dtos/response/subscription_status.py
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GetSubscriptionStatusResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "subscription_id": "018f2b2e-6e2a-7c2e-9a2e-2b2e6e2a7c2e",
                "gateway_status": "ACTIVE",
                "next_due_date": "2026-11-01",
                "value": "129.90",
                "cycle": "MONTHLY",
            }
        }
    )

    subscription_id: UUID
    gateway_status: str
    next_due_date: date
    value: Decimal
    cycle: str
```

- [ ] **Step 4: Adicionar a rota**

Em `app/web/routes/subscriptions.py`, adicionar os imports necessarios no topo (`GetGatewayInfra` de `app.infra.interfaces.gateway_provider`, `GetSubscriptionStatusResponse` do arquivo criado no Step 3) e, apos a rota `cancel_subscription` existente, adicionar:

```python
@router.get(
    "/{subscription_id}",
    response_model=GetSubscriptionStatusResponse,
    summary="Consultar status da assinatura",
    description="""
Consulta o status atual da assinatura direto no gateway (sem cache local).

Util quando o pagador acabou de voltar do checkout e o consumidor quer saber
se o cartao ja foi autorizado, sem esperar o webhook da primeira fatura
(que so chega cerca de 1h depois no Mercado Pago).

### Headers obrigatórios
- `X-System`
- `X-API-Key`
""",
    responses=build_error_responses(401, 403, 404, 429, 500),
)
async def get_subscription_status(
    subscription_id: UUID,
    auth: AuthContext = Depends(require_internal_auth("subscriptions:read")),
    _rate_limiter=Depends(internal_rate_limit()),
    db: AsyncSession = Depends(get_db),
):
    repo = SubscriptionRepositoryINFRA(db)
    try:
        subscription = await repo.get_by_id(subscription_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura nao encontrada.") from exc

    if not subscription.belongs_to_system(auth.system):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura nao encontrada.")

    gateway = GetGatewayInfra().get(gateway=subscription.gateway_provider)
    remote = await gateway.verify_status(subscription.gateway_subscription_id)

    return GetSubscriptionStatusResponse(
        subscription_id=subscription.id,
        gateway_status=remote.status,
        next_due_date=remote.next_due_date,
        value=remote.value,
        cycle=remote.cycle,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_get_subscription_status_endpoint.py -v`
Expected: PASS.

- [ ] **Step 6: Run full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: todos os testes passam.

```bash
git add app/application/dtos/response/subscription_status.py app/web/routes/subscriptions.py tests/test_get_subscription_status_endpoint.py
git commit -m "feat(subscriptions): add GET /v1/subscriptions/{id} to read live gateway status"
```

---

### Task 4: Documentacao e escopo do cliente interno

**Files:**
- Modify: `docs/API.md`
- Modify: `docs/Ambiente.md`
- Modify: `docs/INTEGRATION.md`
- Modify: `docs/agent_memory/mercadopago-adapter-contract.md`

- [ ] **Step 1: Documentar o escopo novo**

Em `docs/Ambiente.md` e `docs/API.md`, junto da lista de scopes existente (`payments:create`, `payments:read`, `subscriptions:create`, `subscriptions:cancel`, `jobs:read`, `customers:create`), adicionar `subscriptions:read` (consultar status ao vivo — `GET /v1/subscriptions/{id}`). Em `docs/API.md`, documentar a rota nova no mesmo formato das outras (scope, exemplo de request/response), ao lado da secao de `POST /v1/subscriptions/{id}/cancel`.

- [ ] **Step 2: Documentar `back_url` em INTEGRATION.md**

No paragrafo de `docs/INTEGRATION.md` que descreve `POST /v1/subscriptions` (comeca em "Assinaturas continuam em..."), acrescentar: "`back_url` e opcional; quando enviado, precisa estar em `ALLOWED_CHECKOUT_REDIRECT_HOSTS` e substitui `MERCADOPAGO_SUBSCRIPTION_BACK_URL` so para essa assinatura."

- [ ] **Step 3: Atualizar `docs/agent_memory/mercadopago-adapter-contract.md`**

Adicionar uma secao curta no fim: "`back_url` por assinatura (2026-09-15): `create_subscription()` aceita `back_url` opcional; quando ausente, cai em `settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL` como antes. `GET /v1/subscriptions/{id}` consulta `verify_status()` ao vivo — nao ha cache local nem webhook novo."

- [ ] **Step 4: Commit**

```bash
git add docs/API.md docs/Ambiente.md docs/INTEGRATION.md docs/agent_memory/mercadopago-adapter-contract.md
git commit -m "docs: document optional back_url and GET /v1/subscriptions/{id}"
```

---

## Self-Review Notes

- Cobertura da spec: back_url opcional (Task 1-2), consulta de status (Task 3), documentacao (Task 4). Nenhum item da spec ficou sem task.
- `INTERNAL_API_CLIENTS` de producao para o `marketfy` precisa ganhar o scope `subscriptions:read` no ambiente real antes do go-live desta mudanca — isso e configuracao de infraestrutura, fora deste repo; sinalizar para quem for publicar (Task 4, Step 1, ja documenta o escopo para quem configurar).
