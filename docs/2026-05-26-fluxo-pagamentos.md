# Fluxo De Pagamentos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** incluir pagamentos avulsos no Billing Core, com criacao idempotente no Asaas, checkout_url para o sistema consumidor, confirmacao por webhook e reconciliacao unica apos 15 minutos, notificacao interna e polling local com intervalo minimo de 10 segundos.

**Architecture:** reutilizar os limites atuais de dominio, aplicacao, infra e web, mantendo o Asaas atras de `InterfaceGateway`. O Billing Core sera a fonte centralizada para sistemas internos consultarem pagamentos; a consulta por polling usara somente o banco local, enquanto o Asaas sera atualizado por webhooks e por um worker diferido de reconciliacao.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis, ARQ, HTTPX, Pydantic 2, Pytest.

---

## Validacao Da Implementacao Atual Contra Asaas

### Fontes oficiais consultadas

- Asaas, "Criar nova cobranca": `POST /v3/payments`, `billingType` aceita `BOLETO`, `PIX`, `CREDIT_CARD` e `UNDEFINED`; uma unica cobranca nao aceita dois `billingType` simultaneos. Fonte: https://docs.asaas.com/reference/criar-nova-cobranca
- Asaas, "Eventos para cobrancas": eventos relevantes incluem `PAYMENT_CREATED`, `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`, `PAYMENT_OVERDUE`, `PAYMENT_REFUNDED` e outros. Fonte: https://docs.asaas.com/docs/webhook-para-cobrancas
- Asaas, "Receba eventos do Asaas no seu endpoint de Webhook": o evento possui `id`, `event` e o objeto da entidade, como `payment`; a autenticacao do webhook usa `asaas-access-token`. Fonte: https://docs.asaas.com/docs/receba-eventos-do-asaas-no-seu-endpoint-de-webhook
- Asaas, "Como implementar idempotencia em Webhooks": entrega e `at least once`; o evento deve ser persistido com ID unico e processado de forma assincrona. Fonte: https://docs.asaas.com/docs/como-implementar-idempotencia-em-webhooks
- Asaas, "Introducao / Webhooks": o Asaas recomenda responder rapidamente com familia 200; se nao houver 200 em 15 tentativas consecutivas, a fila pode ser interrompida. Fonte: https://docs.asaas.com/docs/sobre-os-webhooks
- Asaas, "Recuperar uma unica cobranca": `GET /v3/payments/{id}` deve ser usado para consulta pontual, nao como monitoramento continuo; para mudancas de status, webhooks sao recomendados. Fonte: https://docs.asaas.com/reference/recuperar-uma-unica-cobranca
- Asaas, "Listar cobrancas": a listagem aceita filtro por `externalReference`, mas nao deve ser usada para polling continuo de status. Fonte: https://docs.asaas.com/reference/listar-cobrancas
- Asaas, "Criar novo cliente": a API permite clientes duplicados; integracoes devem prevenir duplicidade por `cpfCnpj`, `externalReference` ou reutilizacao do ID salvo. Fonte: https://docs.asaas.com/reference/criar-novo-cliente
- Asaas, "Autenticacao": a chave deve ser enviada em `access_token`, armazenada fora do codigo, e chave sandbox/producao deve combinar com endpoint sandbox/producao. Fonte: https://docs.asaas.com/docs/autentica%C3%A7%C3%A3o-1

### Aderencias encontradas

- A integracao Asaas usa header `access_token` e `User-Agent` em `app/infra/interfaces/asaas_provider.py`.
- O ambiente impede producao usando endpoint sandbox e exige `INTERNAL_API_CLIENTS` e `ALLOWED_INTERNAL_WEBHOOK_HOSTS` em producao em `app/infra/config.py`.
- A criacao de assinaturas usa `POST /subscriptions` com `customer`, `billingType`, `value`, `nextDueDate`, `cycle` e `description`, alinhado ao guia de assinaturas do Asaas.
- Webhook do Asaas valida `asaas-access-token` com `hmac.compare_digest`, content type JSON, corpo maximo e secret configurado.
- Existe dedupe de webhook por `WebhookEvent.event_id` com unique constraint em `webhook_events.event_id`, alinhado a estrategia oficial de usar ID unico do evento.
- O processamento de webhook e assincrono via ARQ e tem lock por evento no Redis.
- Operacoes criticas internas exigem `Idempotency-Key` e reaproveitam o mesmo job quando a chave e o payload sao iguais.
- Efeitos externos de assinatura possuem dedupe por `gateway_operations.dedupe_key` e estado de reconciliacao quando o gateway cria algo mas a persistencia local falha.
- Entregas para sistemas internos usam HMAC em `X-Webhook-Signature-256` e dedupe em `internal_webhook_deliveries.dedupe_key`.

### Riscos e desalinhamentos

- **Alto:** `POST /v1/webhooks/asaas` responde `202`, mas a documentacao do Asaas afirma que a notificacao e considerada processada com resposta `200`. Recomenda-se alterar a resposta para `200 OK` depois que o evento for validado, persistido ou enfileirado de forma duravel.
- **Alto:** o replay curto na borda retorna `409` para payload duplicado dentro da janela. Para webhooks Asaas, duplicatas deveriam receber familia 200 depois de confirmada a duplicidade, porque a entrega e `at least once` e uma resposta nao-2xx pode manter ou degradar a fila de sincronizacao.
- **Alto:** `ProcessWebhookService` so confirma pagamento quando `PAYMENT_RECEIVED` vem com `details.subscription`. Pagamentos avulsos e eventos de cobranca sem assinatura seriam ignorados, embora o Asaas trate cobrancas como principal recurso de receita.
- **Medio:** o dominio possui `PaymentStatus.PAID`, `FAILED`, `CANCELED`, `EXPIRED`, `REFUNDED`, mas nao possui `CONFIRMED` ou `OVERDUE`; a documentacao Asaas diferencia `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED` e `PAYMENT_OVERDUE`.
- **Medio:** a criacao de assinatura fixa `PaymentType.CREDIT_CARD` no caso de uso, mas o provider converte `DEBIT_CARD` para `UNDEFINED`. Para pagamentos avulsos, a escolha de forma de pagamento precisa ser explicita no contrato.
- **Medio:** nao ha rota de pagamentos avulsos, rota de polling por pagamento, worker de reconciliacao de pagamento pendente apos 15 minutos, nem contrato de notificacao de status de pagamento independente de assinatura.
- **Baixo:** `ASAAS_WEBHOOK_SECRET` e `INTERNAL_WEBHOOK_SIGNATURE` sao obrigatorios, mas a validacao de runtime ainda nao aplica tamanho minimo/ausencia de espaco similares as recomendacoes do Asaas para token de webhook.

## Decisao De Produto Para Pagamentos

- O contrato inicial de pagamentos avulsos deve aceitar `billing_type` com os valores `UNDEFINED`, `PIX`, `BOLETO` e `CREDIT_CARD`.
- Quando o sistema consumidor quiser que o pagador escolha a forma de pagamento, ele deve enviar `billing_type=UNDEFINED`, pois a cobranca regular do Asaas nao permite combinar duas formas como `PIX` e `CREDIT_CARD` na mesma cobranca.
- Se um sistema precisar restringir um subconjunto com multiplas formas simultaneas, isso deve virar uma evolucao separada baseada em checkout/link de pagamento, nao no endpoint regular `/payments`.
- `checkout_url` sera preenchido a partir de `invoiceUrl` retornado pelo Asaas na cobranca.
- `externalReference` enviado ao Asaas deve ser deterministico: `payment:{system}:{system_payment_id}`.

## File Structure

- Modify: `app/domain/enums/payment_status.py` - adicionar estados internos necessarios para espelhar cobrancas Asaas.
- Modify: `app/domain/entities/payment.py` - adicionar factory para pagamento avulso, timestamps e transicoes de status.
- Modify: `app/application/interfaces/gateway_provider.py` - adicionar DTOs e metodos de pagamento no contrato de gateway.
- Modify: `app/infra/interfaces/asaas_provider.py` - implementar `create_payment` e `get_payment`, usando `/payments` e `/payments/{id}`.
- Create: `app/application/dtos/request/payment.py` - DTO de criacao e reconciliacao de pagamento.
- Create: `app/application/dtos/response/payment.py` - DTO de resposta de criacao, consulta e notificacao.
- Create: `app/web/schemas/payment.py` - schemas HTTP para criacao e polling.
- Create: `app/application/use_cases/create_payment.py` - caso de uso de criacao idempotente de pagamento avulso.
- Create: `app/application/use_cases/reconcile_payment.py` - caso de uso que consulta o Asaas pontualmente apos 15 minutos.
- Modify: `app/application/use_cases/process_webhook.py` - processar pagamentos avulsos, `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`, `PAYMENT_OVERDUE` e `PAYMENT_REFUNDED`.
- Modify: `app/workers/tasks.py` - adicionar workers `create_payment_worker`, `reconcile_pending_payment_worker` e generalizar notificacao interna de pagamento.
- Create: `app/web/routes/payments.py` - `POST /v1/payments` e `GET /v1/payments/{payment_id}`.
- Modify: `app/web/routes/webhooks.py` - responder 200 para webhook aceito ou duplicado ja conhecido.
- Modify: `app/web/main.py` - registrar router de pagamentos e tags OpenAPI.
- Modify: `app/infra/config.py` - adicionar escopos documentados e validacoes de segredo.
- Modify: `app/infra/db/models/payment.py` - adicionar `created_at`, `updated_at`, `due_date`, `external_reference` e indice para polling/consulta.
- Create: `alembic/versions/20260526_000001_payments_flow.py` - migracao para novos campos e indices.
- Modify: `docs/API.md` - documentar endpoints de pagamento.
- Modify: `docs/Webhooks.md` - documentar eventos de cobranca avulsa e resposta 200.
- Test: `tests/test_create_payment_use_case.py`.
- Test: `tests/test_reconcile_payment_use_case.py`.
- Test: `tests/test_process_webhook_use_case.py`.
- Test: `tests/test_api_contracts.py`.

---

### Task 1: Domain Model For Standalone Payments

**Files:**
- Modify: `app/domain/enums/payment_status.py`
- Modify: `app/domain/entities/payment.py`
- Test: `tests/test_domain_entities.py`

- [ ] **Step 1: Write failing tests for new payment status transitions**

Add these tests to `tests/test_domain_entities.py`:

```python
from datetime import date

def test_standalone_payment_can_be_confirmed_and_received():
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=date(2026, 6, 10),
        external_reference="payment:neectify_shop:order-123",
    )

    payment.mark_as_confirmed(datetime(2026, 6, 10, tzinfo=timezone.utc), Decimal("77.90"))
    assert payment.payment_status == PaymentStatus.CONFIRMED
    assert payment.net_value == Decimal("77.90")

    payment.mark_as_paid(datetime(2026, 6, 11, tzinfo=timezone.utc), Decimal("77.90"))
    assert payment.payment_status == PaymentStatus.PAID
    assert payment.paid_date == datetime(2026, 6, 11, tzinfo=timezone.utc)


def test_standalone_payment_can_be_marked_overdue_before_payment():
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=date(2026, 6, 10),
        external_reference="payment:neectify_shop:order-123",
    )

    payment.mark_as_overdue()

    assert payment.payment_status == PaymentStatus.OVERDUE
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
python -m pytest tests/test_domain_entities.py -q
```

Expected: FAIL because `PaymentStatus.CONFIRMED`, `PaymentStatus.OVERDUE`, `Payment.create_standalone_payment`, `mark_as_confirmed` and `mark_as_overdue` do not exist yet.

- [ ] **Step 3: Add statuses**

Change `app/domain/enums/payment_status.py` to:

```python
from enum import Enum


class PaymentStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PAID = "paid"
    OVERDUE = "overdue"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REFUNDED = "refunded"
```

- [ ] **Step 4: Add standalone payment fields and transitions**

In `app/domain/entities/payment.py`, extend the constructor with:

```python
due_date: date | None = None,
external_reference: str | None = None,
created_at: datetime | None = None,
updated_at: datetime | None = None,
```

Set:

```python
self.due_date = due_date
self.external_reference = external_reference.strip() if external_reference else None
self.created_at = created_at or datetime.now(timezone.utc)
self.updated_at = updated_at or self.created_at
```

Add:

```python
@classmethod
def create_standalone_payment(
    cls,
    *,
    description: str,
    gateway: GatewayProvider,
    system_payment_id: str,
    provider_payment_id: str,
    value: Decimal,
    from_system: System,
    checkout_link: str | None,
    webhook_link: str | None,
    due_date: date,
    external_reference: str,
) -> "Payment":
    return cls(
        description=description,
        gateway=gateway,
        system_payment_id=system_payment_id,
        provider_payment_id=provider_payment_id,
        value=value,
        from_system=from_system,
        checkout_link=checkout_link,
        webhook_link=webhook_link,
        due_date=due_date,
        external_reference=external_reference,
        movimentation_type=MovimentationType.DEFAULT_PAYMENT,
    )

def mark_as_confirmed(self, payment_date: datetime | None = None, net_value: Decimal | None = None):
    if self.payment_status in {PaymentStatus.CONFIRMED, PaymentStatus.PAID}:
        return
    if self.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
        raise DomainError("Nao e possivel confirmar esse pagamento.")
    self.payment_status = PaymentStatus.CONFIRMED
    self.paid_date = payment_date or datetime.now(timezone.utc)
    if net_value is not None:
        if net_value < 0:
            raise DomainError("Pagamento nao pode ter valor liquido negativo.")
        self.net_value = net_value
    self.updated_at = datetime.now(timezone.utc)

def mark_as_overdue(self):
    if self.payment_status == PaymentStatus.PAID:
        raise DomainError("Nao e possivel vencer um pagamento ja recebido.")
    if self.payment_status in {PaymentStatus.CANCELED, PaymentStatus.REFUNDED, PaymentStatus.FAILED}:
        raise DomainError("Nao e possivel vencer esse pagamento.")
    self.payment_status = PaymentStatus.OVERDUE
    self.updated_at = datetime.now(timezone.utc)
```

Update `mark_as_paid`, `mark_as_refunded`, `mark_as_failed` and `mark_as_canceled` to set `updated_at = datetime.now(timezone.utc)` when state changes. Permit `mark_as_paid` from `PENDING`, `OVERDUE` and `CONFIRMED`.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_domain_entities.py -q
```

Expected: PASS.

---

### Task 2: Database Migration And Repository Persistence

**Files:**
- Modify: `app/infra/db/models/payment.py`
- Modify: `app/infra/repo/payment_repo.py`
- Create: `alembic/versions/20260526_000001_payments_flow.py`
- Test: existing repository coverage through API/use-case tests

- [ ] **Step 1: Add payment model fields**

Add columns to `PaymentModel`:

```python
due_date: Mapped[date] = mapped_column(Date, nullable=True, index=True)
external_reference: Mapped[str] = mapped_column(String(255), nullable=True, index=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Add indices:

```python
Index("ix_payments_system_status_created", "from_system", "payment_status", "created_at"),
Index("ix_payments_system_external_reference", "from_system", "external_reference"),
```

Pass the new fields in `to_domain()`.

- [ ] **Step 2: Persist new fields in repository**

In `PaymentRepositoryINFRA.save`, set `due_date`, `external_reference`, `created_at` and `updated_at` on insert and update.

- [ ] **Step 3: Create Alembic migration**

Create `alembic/versions/20260526_000001_payments_flow.py`:

```python
"""payments flow

Revision ID: 20260526_000001
Revises: 20260424_000004
Create Date: 2026-05-26 00:00:01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260526_000001"
down_revision: Union[str, None] = "20260424_000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("payments", sa.Column("external_reference", sa.String(length=255), nullable=True))
    op.add_column("payments", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("payments", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_payments_due_date", "payments", ["due_date"], unique=False)
    op.create_index("ix_payments_external_reference", "payments", ["external_reference"], unique=False)
    op.create_index("ix_payments_system_status_created", "payments", ["from_system", "payment_status", "created_at"], unique=False)
    op.create_index("ix_payments_system_external_reference", "payments", ["from_system", "external_reference"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payments_system_external_reference", table_name="payments")
    op.drop_index("ix_payments_system_status_created", table_name="payments")
    op.drop_index("ix_payments_external_reference", table_name="payments")
    op.drop_index("ix_payments_due_date", table_name="payments")
    op.drop_column("payments", "updated_at")
    op.drop_column("payments", "created_at")
    op.drop_column("payments", "external_reference")
    op.drop_column("payments", "due_date")
```

- [ ] **Step 4: Validate migration graph**

Run:

```powershell
python -m alembic heads
python -m alembic upgrade head
```

Expected: one head and successful migration.

---

### Task 3: Gateway Contract For Payments

**Files:**
- Modify: `app/application/interfaces/gateway_provider.py`
- Modify: `app/infra/interfaces/asaas_provider.py`
- Test: `tests/test_create_payment_use_case.py`

- [ ] **Step 1: Add gateway DTOs**

In `app/application/interfaces/gateway_provider.py`, add:

```python
@dataclass
class CreatePaymentGatewayResponse:
    payment_id: str
    status: str
    value: Decimal
    due_date: date
    invoice_url: str | None
    billing_type: str
    external_reference: str | None


@dataclass
class PaymentStatusGatewayResponse:
    payment_id: str
    status: str
    value: Decimal
    net_value: Decimal | None
    due_date: date | None
    payment_date: date | None
    invoice_url: str | None
    billing_type: str
    external_reference: str | None
```

Add abstract methods:

```python
@abstractmethod
async def create_payment(
    self,
    customer_provider_id: str,
    billing_type: PaymentType,
    value: Decimal,
    due_date: date,
    description: str,
    external_reference: str,
) -> CreatePaymentGatewayResponse:
    pass

@abstractmethod
async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
    pass
```

- [ ] **Step 2: Implement Asaas payment creation**

In `app/infra/interfaces/asaas_provider.py`, add:

```python
async def create_payment(
    self,
    customer_provider_id: str,
    billing_type: PaymentType,
    value: Decimal,
    due_date: date,
    description: str,
    external_reference: str,
) -> CreatePaymentGatewayResponse:
    payload = {
        "customer": customer_provider_id,
        "billingType": billing_type.value,
        "value": float(value),
        "dueDate": due_date.isoformat(),
        "description": description,
        "externalReference": external_reference,
    }
    response = await self.asaas.post("/payments", payload)
    return CreatePaymentGatewayResponse(
        payment_id=response["id"],
        status=response["status"],
        value=Decimal(str(response["value"])),
        due_date=date.fromisoformat(response["dueDate"]),
        invoice_url=response.get("invoiceUrl"),
        billing_type=response["billingType"],
        external_reference=response.get("externalReference"),
    )
```

- [ ] **Step 3: Implement Asaas payment retrieval**

Add:

```python
async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
    response = await self.asaas.get(f"/payments/{payment_id}")
    payment_date = response.get("paymentDate")
    return PaymentStatusGatewayResponse(
        payment_id=response["id"],
        status=response["status"],
        value=Decimal(str(response["value"])),
        net_value=Decimal(str(response["netValue"])) if response.get("netValue") is not None else None,
        due_date=date.fromisoformat(response["dueDate"]) if response.get("dueDate") else None,
        payment_date=date.fromisoformat(payment_date) if payment_date else None,
        invoice_url=response.get("invoiceUrl"),
        billing_type=response["billingType"],
        external_reference=response.get("externalReference"),
    )
```

- [ ] **Step 4: Add a unit fake for use-case tests**

In `tests/test_create_payment_use_case.py`, use fake gateway methods with the same method names and return dataclass-shaped objects from the interface.

---

### Task 4: Create Payment Use Case

**Files:**
- Create: `app/application/dtos/request/payment.py`
- Create: `app/application/dtos/response/payment.py`
- Create: `app/application/use_cases/create_payment.py`
- Test: `tests/test_create_payment_use_case.py`

- [ ] **Step 1: Create request and response DTOs**

Create `app/application/dtos/request/payment.py`:

```python
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class CreatePaymentDTO(BaseModel):
    customer_provider_id: str
    value: Decimal
    billing_type: PaymentType
    due_date: date
    description: str
    system: System
    system_payment_id: str
    webhook_link: str
```

Create `app/application/dtos/response/payment.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType


class CreatePaymentResponse(BaseModel):
    payment_id: UUID
    value: Decimal
    checkout_url: str | None
    payment_status: PaymentStatus
    billing_type: PaymentType
    due_date: date


class PaymentStatusResponse(BaseModel):
    payment_id: UUID
    system_payment_id: str
    value: Decimal
    checkout_url: str | None
    payment_status: PaymentStatus
    billing_type: PaymentType
    due_date: date | None
    paid_date: datetime | None
    updated_at: datetime
```

- [ ] **Step 2: Write failing create-payment tests**

Create `tests/test_create_payment_use_case.py` with tests for:

```python
@pytest.mark.asyncio
async def test_create_payment_persists_gateway_payment_and_returns_checkout_url():
    ...
    assert response.checkout_url == "https://www.asaas.com/i/pay_123"
    assert gateway.create_payment_called == 1
    assert payment_repo.saved[0].system_payment_id == "order-123"
    assert payment_repo.saved[0].external_reference == "payment:neectify_shop:order-123"


@pytest.mark.asyncio
async def test_create_payment_reuses_existing_local_payment_without_gateway_call():
    ...
    assert gateway.create_payment_called == 0


@pytest.mark.asyncio
async def test_create_payment_marks_operation_for_reconciliation_when_local_save_fails_after_gateway_create():
    ...
    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.REQUIRES_RECONCILIATION
```

- [ ] **Step 3: Implement use case**

Create `app/application/use_cases/create_payment.py`:

```python
from app.application.dtos.request.payment import CreatePaymentDTO
from app.application.dtos.response.payment import CreatePaymentResponse
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.customer import Customer
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.payment_status import PaymentStatus
from app.domain.errors import DomainError


class CreatePayment:
    def __init__(self, get_gateway: GetGateway, uow: UowProvider, payment_repo: PaymentRepository, gateway_operation_repo: GatewayOperationRepository):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo
        self.gateway_operation_repo = gateway_operation_repo

    async def execute(self, request: CreatePaymentDTO, customer: Customer) -> CreatePaymentResponse:
        if request.system != customer.system:
            raise DomainError("Sistema do pagamento difere do customer informado.")
        if not customer.has_provider_binding():
            raise DomainError("Customer sem vinculacao no gateway para criacao de pagamento.")

        existing_payment = await self.payment_repo.get_by_system_id(request.system_payment_id)
        if existing_payment:
            return CreatePaymentResponse(
                payment_id=existing_payment.id,
                value=existing_payment.value,
                checkout_url=existing_payment.checkout_link,
                payment_status=existing_payment.payment_status,
                billing_type=existing_payment.payment_type,
                due_date=existing_payment.due_date,
            )

        external_reference = f"payment:{request.system.value}:{request.system_payment_id}"
        operation_dedupe_key = f"create_payment:{request.system.value}:{request.system_payment_id}"
        existing_operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)
        if existing_operation:
            if existing_operation.status == GatewayOperationStatus.COMPLETED:
                raise DomainError("Existe uma operacao concluida sem espelho local consistente. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
                raise DomainError("Existe uma operacao pendente de reconciliacao para esse pagamento.")
            raise DomainError("Ja existe uma operacao de criacao de pagamento em andamento ou falha recente para essa referencia.")

        operation = GatewayOperation(
            operation_name="create_payment",
            dedupe_key=operation_dedupe_key,
            provider=customer.gateway_provider,
            system=request.system,
            request_payload=request.model_dump(mode="json"),
        )
        operation = await self.gateway_operation_repo.save(operation)
        await self.uow.commit()

        gateway = self.get_gateway.get(gateway=customer.gateway_provider)
        gateway_payment_id = None
        try:
            payment_info = await gateway.create_payment(
                customer_provider_id=customer.provider_customer_id,
                billing_type=request.billing_type,
                value=request.value,
                due_date=request.due_date,
                description=request.description,
                external_reference=external_reference,
            )
            gateway_payment_id = payment_info.payment_id
            payment = Payment.create_standalone_payment(
                description=request.description,
                gateway=customer.gateway_provider,
                system_payment_id=request.system_payment_id,
                provider_payment_id=payment_info.payment_id,
                value=payment_info.value,
                from_system=request.system,
                checkout_link=payment_info.invoice_url,
                webhook_link=request.webhook_link,
                due_date=payment_info.due_date,
                external_reference=external_reference,
            )
            payment.payment_status = PaymentStatus.PENDING
            payment.payment_type = request.billing_type
            payment = await self.payment_repo.save(payment)
            operation.mark_completed(gateway_reference=gateway_payment_id)
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            return CreatePaymentResponse(
                payment_id=payment.id,
                value=payment.value,
                checkout_url=payment.checkout_link,
                payment_status=payment.payment_status,
                billing_type=payment.payment_type,
                due_date=payment.due_date,
            )
        except Exception as exc:
            await self.uow.rollback()
            if gateway_payment_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_payment_id, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                raise DomainError("Pagamento criado no gateway, mas a sincronizacao local falhou. Operacao marcada para reconciliacao.") from exc
            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
```

- [ ] **Step 4: Run use-case tests**

Run:

```powershell
python -m pytest tests/test_create_payment_use_case.py -q
```

Expected: PASS.

---

### Task 5: Payment HTTP API And Idempotency

**Files:**
- Create: `app/web/schemas/payment.py`
- Create: `app/web/routes/payments.py`
- Modify: `app/web/main.py`
- Modify: `.env.example`
- Modify: `docs/API.md`
- Test: `tests/test_api_contracts.py`

- [ ] **Step 1: Add schemas**

Create `app/web/schemas/payment.py` with `CreatePaymentRequest`, mirroring `CreateSubscriptionRequest` validation:

```python
class CreatePaymentRequest(BaseModel):
    customer_provider_id: str = Field(..., min_length=1, max_length=128)
    value: Decimal = Field(..., gt=0)
    billing_type: PaymentType = Field(default=PaymentType.UNDEFINED)
    due_date: date
    description: str = Field(..., min_length=1, max_length=255)
    system: System
    system_payment_id: str = Field(..., min_length=1, max_length=128)
    webhook_link: str = Field(..., max_length=2048)
```

Validation rules:

```python
@field_validator("billing_type")
@classmethod
def validate_allowed_billing_type(cls, value: PaymentType) -> PaymentType:
    if value == PaymentType.DEBIT_CARD:
        raise ValueError("DEBIT_CARD nao e suportado para cobranca avulsa Asaas.")
    return value
```

Use the same HTTPS and allowed-host validation from subscription schema.

- [ ] **Step 2: Add create route**

Create `app/web/routes/payments.py` with:

```python
router = APIRouter(prefix="/v1/payments", tags=["payments"])

@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedJobResponse)
async def create_payment(
    payload: CreatePaymentRequest,
    http_request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_internal_auth("payments:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    if auth.system != payload.system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sistema sem permissao para criar esse pagamento.")
    request_hash = build_request_hash(payload.model_dump(mode="json"))
    namespace = "payment_create"
    existing_job = await start_idempotent_job(redis, auth.system, idempotency_key, request_hash, namespace=namespace)
    if existing_job:
        return {"job_id": existing_job["job_id"], "message": "Pagamento ja recebido anteriormente. Retornando job existente."}
    try:
        job = await redis.enqueue_job(
            "workers:tasks.create_payment_worker",
            payload.model_dump(mode="json", exclude={"customer_provider_id"}),
            payload.customer_provider_id,
            payload.system.name,
        )
    except Exception:
        await clear_idempotent_job(redis, auth.system, idempotency_key, namespace=namespace)
        raise
    await save_idempotent_job(redis, auth.system, idempotency_key, request_hash, job.job_id, namespace=namespace)
    await redis.setex(f"billing_core:job_owner:{job.job_id}", settings.JOB_METADATA_TTL_SECONDS, auth.system.value)
    await update_job_metadata(redis, job.job_id, status="queued", job_name="create_payment_worker", attempt=0, max_tries=settings.WORKER_MAX_TRIES, request_id=http_request.state.request_id, created_at=datetime.now(timezone.utc), system=auth.system.value, resource_type="payment")
    return {"job_id": job.job_id, "message": "Pagamento enviado para processamento."}
```

- [ ] **Step 3: Add polling route with 10 second interval**

In `app/web/routes/payments.py`, add:

```python
@router.get("/{payment_id}", response_model=PaymentStatusResponse)
async def get_payment_status(
    payment_id: UUID,
    response: Response,
    auth: AuthContext = Depends(require_internal_auth("payments:read")),
    redis=Depends(get_redis_pool),
    db: AsyncSession = Depends(get_db),
):
    polling_key = f"billing_core:payment_poll:{auth.system.value}:{payment_id}"
    allowed = await redis.set(polling_key, "1", ex=10, nx=True)
    if not allowed:
        response.headers["Retry-After"] = "10"
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Consulte esse pagamento em intervalos minimos de 10 segundos.")

    repo = PaymentRepositoryINFRA(db)
    payment = await repo.get_by_id(payment_id)
    if payment.from_system != auth.system:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento nao encontrado.")
    return PaymentStatusResponse(
        payment_id=payment.id,
        system_payment_id=payment.system_payment_id,
        value=payment.value,
        checkout_url=payment.checkout_link,
        payment_status=payment.payment_status,
        billing_type=payment.payment_type,
        due_date=payment.due_date,
        paid_date=payment.paid_date,
        updated_at=payment.updated_at,
    )
```

- [ ] **Step 4: Register router and scopes**

In `app/web/main.py`, include `payments_router`. In docs and `.env.example`, add scopes:

```text
payments:create
payments:read
```

- [ ] **Step 5: Add API contract tests**

Add tests to `tests/test_api_contracts.py` for:

```python
def test_create_payment_requires_idempotency_key(client): ...
def test_create_payment_is_idempotent_per_key_and_payload(client): ...
def test_create_payment_rejects_debit_card(client): ...
def test_payment_polling_enforces_ten_second_interval(client): ...
def test_payment_polling_hides_other_system_payment(client): ...
```

- [ ] **Step 6: Run API contract tests**

Run:

```powershell
python -m pytest tests/test_api_contracts.py -q
```

Expected: PASS.

---

### Task 6: Workers For Creation And 15 Minute Reconciliation

**Files:**
- Modify: `app/workers/tasks.py`
- Modify: `app/workers/worker.py`
- Create: `app/application/use_cases/reconcile_payment.py`
- Test: `tests/test_reconcile_payment_use_case.py`

- [ ] **Step 1: Implement status mapper**

Create a helper in `app/application/use_cases/reconcile_payment.py`:

```python
def apply_gateway_payment_status(payment: Payment, status: str, payment_date: date | None, net_value: Decimal | None) -> bool:
    before = payment.payment_status
    normalized = status.upper()
    dt = datetime.combine(payment_date, time.min, tzinfo=timezone.utc) if payment_date else None
    if normalized == "CONFIRMED":
        payment.mark_as_confirmed(dt, net_value)
    elif normalized == "RECEIVED":
        payment.mark_as_paid(dt, net_value)
    elif normalized == "OVERDUE":
        payment.mark_as_overdue()
    elif normalized in {"REFUNDED", "CHARGEBACK_REQUESTED"}:
        if payment.payment_status in {PaymentStatus.PAID, PaymentStatus.CONFIRMED}:
            payment.mark_as_refunded()
    elif normalized in {"DELETED"}:
        if payment.payment_status in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
            payment.mark_as_canceled()
    return before != payment.payment_status
```

- [ ] **Step 2: Implement reconcile use case**

Create:

```python
class ReconcilePayment:
    def __init__(self, get_gateway: GetGateway, uow: UowProvider, payment_repo: PaymentRepository):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo

    async def execute(self, payment_id: UUID) -> Payment | None:
        payment = await self.payment_repo.get_by_id(payment_id)
        if payment.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.CONFIRMED}:
            return None
        gateway = self.get_gateway.get(payment.gateway)
        remote = await gateway.get_payment(payment.provider_payment_id)
        changed = apply_gateway_payment_status(payment, remote.status, remote.payment_date, remote.net_value)
        if not changed:
            return None
        payment = await self.payment_repo.save(payment)
        await self.uow.commit()
        return payment
```

- [ ] **Step 3: Add create payment worker**

In `app/workers/tasks.py`, add `create_payment_worker`. After `CreatePayment.execute` succeeds, enqueue:

```python
await ctx["redis"].enqueue_job(
    "workers:tasks.reconcile_pending_payment_worker",
    str(result.payment_id),
    _defer_by=900,
)
```

Also enqueue internal notification if the initial status differs from pending.

- [ ] **Step 4: Add reconciliation worker**

Add:

```python
async def reconcile_pending_payment_worker(ctx, payment_id: str):
    ...
    result = await service.execute(UUID(payment_id))
    if result is not None:
        delivery = await _build_payment_internal_delivery(result)
        ...
```

This worker performs exactly one Asaas `GET /payments/{id}` after 15 minutes. It must not reschedule continuous polling.

- [ ] **Step 5: Register workers**

In `app/workers/worker.py`, import and register:

```python
create_payment_worker
reconcile_pending_payment_worker
```

- [ ] **Step 6: Run worker tests**

Run:

```powershell
python -m pytest tests/test_reconcile_payment_use_case.py -q
```

Expected: PASS.

---

### Task 7: Webhook Processing For Standalone Payments

**Files:**
- Modify: `app/application/dtos/request/webhook.py`
- Modify: `app/application/dtos/response/webhook.py`
- Modify: `app/application/use_cases/process_webhook.py`
- Modify: `app/web/routes/webhooks.py`
- Test: `tests/test_process_webhook_use_case.py`
- Test: `tests/test_api_contracts.py`

- [ ] **Step 1: Add missing event type**

In `EventType`, add:

```python
PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
PAYMENT_DELETED = "PAYMENT_DELETED"
```

- [ ] **Step 2: Add internal payment event response**

In `app/application/dtos/response/webhook.py`, add:

```python
PAYMENT_STATUS_UPDATED = "PAYMENT_STATUS_UPDATED"
```

- [ ] **Step 3: Write standalone webhook tests**

Add tests:

```python
@pytest.mark.asyncio
async def test_process_webhook_marks_standalone_payment_as_paid():
    payment = make_standalone_payment(provider_payment_id="pay-1")
    service = ProcessWebhookService(payment_repo=FakePaymentRepo(existing=payment), sub_repo=FakeSubscriptionRepo(None), uow=FakeUow(), webhook_event_repo=FakeWebhookEventRepo())
    payload = WebhookPayload(event=EventType.PAYMENT_RECEIVED, source_event_id="evt-standalone-1", details=Details(id="pay-1", subscription=None, status="RECEIVED", value=Decimal("79.90"), net_value=Decimal("77.90"), payment_date=datetime.now(timezone.utc), external_reference="payment:neectify_shop:order-123"))

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert payment.payment_status == PaymentStatus.PAID
    assert response.event == InternalEventType.PAYMENT_STATUS_UPDATED
    assert response.payment_id == payment.id
```

- [ ] **Step 4: Process standalone payments**

In `ProcessWebhookService.execute`, before subscription-specific logic, add:

```python
if payload.details.id and not payload.details.subscription:
    payment = await self.payment_repo.get_by_provider_id(payload.details.id)
    if payment is None:
        event.mark_as_processed()
        await self.webhook_event_repo.save(event)
        await self.uow.commit()
        return None
    changed = apply_gateway_payment_status(payment, payload.details.status or payload.event.value.removeprefix("PAYMENT_"), payload.details.payment_date.date() if payload.details.payment_date else None, payload.details.net_value)
    if changed:
        payment = await self.payment_repo.save(payment)
    event.mark_as_processed()
    await self.webhook_event_repo.save(event)
    await self.uow.commit()
    if not changed:
        return None
    return ProcessWebhookResponse(event=InternalEventType.PAYMENT_STATUS_UPDATED, payment_id=payment.id, subscription_id=None)
```

- [ ] **Step 5: Return 200 for accepted Asaas webhook**

Change route decorator in `app/web/routes/webhooks.py` from `status_code=status.HTTP_202_ACCEPTED` to `status_code=status.HTTP_200_OK`. For duplicate replay, change `validate_asaas_webhook` behavior so duplicate returns a signal that the route can respond with:

```json
{"received": true, "duplicate": true}
```

The endpoint must return a 2xx response for duplicates already identified as same raw payload in the replay window.

- [ ] **Step 6: Run webhook tests**

Run:

```powershell
python -m pytest tests/test_process_webhook_use_case.py tests/test_api_contracts.py -q
```

Expected: PASS.

---

### Task 8: Internal Payment Notifications

**Files:**
- Modify: `app/application/dtos/response/webhook.py`
- Modify: `app/workers/tasks.py`
- Modify: `app/infra/interfaces/internal_webhook.py`
- Test: `tests/test_process_webhook_use_case.py`

- [ ] **Step 1: Define payment notification payload**

Add:

```python
class SendInternalWebhookPayment(BaseModel):
    event: InternalEventType
    payment_id: UUID
    system_payment_id: str
    payment_status: PaymentStatus
    value: Decimal
    paid_date: date | None = None
    checkout_url: str | None = None
```

- [ ] **Step 2: Build payment delivery**

Add `_build_payment_internal_delivery(payment)` in `app/workers/tasks.py`:

```python
payload = SendInternalWebhookPayment(
    event=InternalEventType.PAYMENT_STATUS_UPDATED,
    payment_id=payment.id,
    system_payment_id=payment.system_payment_id,
    payment_status=payment.payment_status,
    value=payment.value,
    paid_date=payment.paid_date.date() if payment.paid_date else None,
    checkout_url=payment.checkout_link,
)
dedupe_key = f"payment:{payment.id}:{payment.payment_status.value}:{payment.updated_at.isoformat()}"
return InternalWebhookDelivery(
    dedupe_key=dedupe_key,
    event_type=payload.event.value,
    target_url=payment.webhook_link,
    payload=payload.model_dump(mode="json"),
    subscription_id=None,
    payment_id=payment.id,
)
```

If `internal_webhook_deliveries.subscription_id` remains non-nullable, create a migration making it nullable because standalone payment notifications have no subscription.

- [ ] **Step 3: Add idempotency headers to internal webhooks**

In `InternalWebhookProvider.send`, include:

```python
"X-Webhook-Id": delivery_id_or_dedupe_key,
"X-Webhook-Event": event_type,
```

If changing the method signature is necessary, use:

```python
async def send(self, url: str, payload, webhook_id: str | None = None, event_type: str | None = None) -> dict | None:
```

- [ ] **Step 4: Ensure retries stay idempotent**

Retries must reuse the same `InternalWebhookDelivery.id`, `X-Webhook-Id` and payload. Consumers should dedupe by `X-Webhook-Id`.

---

### Task 9: Security, Performance, And Integration Hardening

**Files:**
- Modify: `app/infra/config.py`
- Modify: `app/web/dependencies/rate_limit.py`
- Modify: `scripts/preflight_production_check.py`
- Modify: `docs/Ambiente.md`
- Modify: `docs/Onboarding_SaaS.md`

- [ ] **Step 1: Validate secrets at runtime**

In `Settings.validate_runtime`, add:

```python
for secret_name, secret_value in {
    "ASAAS_WEBHOOK_SECRET": self.ASAAS_WEBHOOK_SECRET,
    "INTERNAL_WEBHOOK_SIGNATURE": self.INTERNAL_WEBHOOK_SIGNATURE,
}.items():
    if len(secret_value.strip()) < 32:
        raise RuntimeError(f"Configuracao invalida: {secret_name} deve ter pelo menos 32 caracteres.")
    if any(ch.isspace() for ch in secret_value):
        raise RuntimeError(f"Configuracao invalida: {secret_name} nao pode conter espacos.")
```

- [ ] **Step 2: Add payment polling rate limit helper**

Keep the polling guard per payment at 10 seconds. Do not reuse the general internal rate limit for the 10-second rule because the requirement is per payment resource.

- [ ] **Step 3: Document integration contract**

In `docs/Onboarding_SaaS.md`, add a payment section:

```markdown
1. Criar customer no gateway ou reutilizar `customer_provider_id`.
2. Chamar `POST /v1/payments` com `Idempotency-Key`.
3. Ler `job_id` e consultar `GET /v1/jobs/{job_id}` ate obter o resultado.
4. Redirecionar o usuario para `checkout_url`.
5. Receber webhook interno assinado para mudancas de status.
6. Se necessario, consultar `GET /v1/payments/{payment_id}` respeitando 10 segundos entre chamadas para o mesmo pagamento.
```

- [ ] **Step 4: Document Asaas limitations**

In `docs/API.md`, state:

```markdown
Para permitir que o pagador escolha a forma de pagamento, envie `billing_type=UNDEFINED`.
O endpoint regular de cobranca do Asaas nao aceita multiplos `billingType` em uma unica cobranca.
```

---

### Task 10: Final Verification

**Files:**
- All modified files

- [ ] **Step 1: Run unit and contract tests**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run production preflight**

Run:

```powershell
python scripts/preflight_production_check.py --run-tests --check-migrations
```

Expected: preflight passes.

- [ ] **Step 3: Manual smoke for payment creation**

Run with valid env:

```powershell
$headers = @{
  "X-System" = "neectify_shop"
  "X-API-Key" = "fake-neectify-shop-key"
  "Idempotency-Key" = "payment-order-123"
  "Content-Type" = "application/json"
}
$body = @{
  customer_provider_id = "cus_123"
  value = "79.90"
  billing_type = "UNDEFINED"
  due_date = "2026-06-10"
  description = "Pedido 123"
  system = "neectify_shop"
  system_payment_id = "order-123"
  webhook_link = "https://hooks.neectify.local/billing/payment"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v1/payments" -Headers $headers -Body $body
```

Expected: `202` with `job_id`. Job result contains `checkout_url`.

- [ ] **Step 4: Manual smoke for polling**

Run:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/v1/payments/11111111-1111-1111-1111-111111111111" -Headers @{
  "X-System" = "neectify_shop"
  "X-API-Key" = "fake-neectify-shop-key"
}
```

Expected: replace `11111111-1111-1111-1111-111111111111` with the `payment_id` returned by the job result; first request returns `200`; a second request for the same payment within 10 seconds returns `429` and `Retry-After: 10`.

## Completion Criteria

- Pagamentos avulsos podem ser criados por sistemas internos com `Idempotency-Key`.
- O Asaas recebe `externalReference` deterministico.
- O resultado de criacao disponibiliza `checkout_url`.
- O sistema consumidor escolhe uma forma unica ou `UNDEFINED` para permitir escolha pelo pagador.
- Webhooks Asaas de cobranca atualizam pagamentos avulsos e respondem familia 200 quando aceitos ou duplicados.
- Um worker consulta o Asaas uma unica vez apos 15 minutos se o pagamento ainda estiver pendente.
- Notificacoes internas de status de pagamento sao assinadas, idempotentes e entregues com retry.
- Polling de status consulta somente estado local e aplica intervalo minimo de 10 segundos por pagamento e sistema.
- Testes e preflight passam.
