from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.payment import CreatePaymentDTO
from app.application.interfaces.gateway_provider import CreatePaymentGatewayResponse
from app.application.use_cases.create_payment import CreatePayment
from app.domain.entities.customer import Customer
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System
from app.domain.errors import DomainError
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email


class FakeGateway:
    def __init__(self):
        self.create_payment_called = 0

    async def create_payment(self, **kwargs):
        self.create_payment_called += 1
        return CreatePaymentGatewayResponse(
            payment_id="pay_123",
            status="PENDING",
            value=Decimal("79.90"),
            due_date=date(2026, 6, 10),
            invoice_url="https://www.asaas.com/i/pay_123",
            billing_type="UNDEFINED",
            external_reference=kwargs["external_reference"],
        )


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakePaymentRepo:
    def __init__(self, existing: Payment | None = None, fail_on_save: bool = False):
        self.existing = existing
        self.fail_on_save = fail_on_save
        self.saved: list[Payment] = []

    async def get_by_system_id(self, system_id: str):
        if self.existing and self.existing.system_payment_id == system_id:
            return self.existing
        return None

    async def get_by_system_ref(self, system_id: str, system: System):
        if self.existing and self.existing.system_payment_id == system_id and self.existing.from_system == system:
            return self.existing
        return None

    async def save(self, payment: Payment):
        if self.fail_on_save:
            raise RuntimeError("local save failed")
        if payment.id is None:
            payment.id = uuid4()
        self.saved.append(payment)
        self.existing = payment
        return payment


class FakeGatewayOperationRepo:
    def __init__(self, existing: GatewayOperation | None = None):
        self.existing = existing
        self.saved: list[GatewayOperation] = []

    async def get_by_dedupe_key(self, dedupe_key: str):
        if self.existing and self.existing.dedupe_key == dedupe_key:
            return self.existing
        return None

    async def save(self, operation: GatewayOperation):
        if operation.id is None:
            operation.id = uuid4()
        self.saved.append(operation)
        self.existing = operation
        return operation


class FakeUow:
    def __init__(self):
        self.commit_called = 0
        self.rollback_called = 0

    async def commit(self):
        self.commit_called += 1

    async def rollback(self):
        self.rollback_called += 1


def make_customer() -> Customer:
    return Customer(
        nome="Carlos",
        email=Email("carlos@example.com"),
        cpf=CPF("39053344705"),
        provider_customer_id="cus_123",
        system_customer_id="cus-1",
        gateway_provider=GatewayProvider.ASAAS,
        system=System.NEECTIFY_SHOP,
    )


def make_request() -> CreatePaymentDTO:
    return CreatePaymentDTO(
        customer_provider_id="cus_123",
        value=Decimal("79.90"),
        billing_type=PaymentType.UNDEFINED,
        due_date=date(2026, 6, 10),
        description="Pedido 123",
        system=System.NEECTIFY_SHOP,
        system_payment_id="order-123",
        webhook_link="https://hooks.neectify.local/billing/payment",
    )


@pytest.mark.asyncio
async def test_create_payment_persists_gateway_payment_and_returns_checkout_url():
    gateway = FakeGateway()
    payment_repo = FakePaymentRepo()
    service = CreatePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=payment_repo,
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    response = await service.execute(make_request(), make_customer())

    assert response.checkout_url == "https://www.asaas.com/i/pay_123"
    assert gateway.create_payment_called == 1
    assert payment_repo.saved[0].system_payment_id == "order-123"
    assert payment_repo.saved[0].external_reference == "payment:neectify_shop:order-123"


@pytest.mark.asyncio
async def test_create_payment_reuses_existing_local_payment_without_gateway_call():
    existing = Payment.create_standalone_payment(
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
    existing.id = uuid4()
    existing.payment_type = PaymentType.UNDEFINED
    gateway = FakeGateway()
    service = CreatePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(existing=existing),
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    response = await service.execute(make_request(), make_customer())

    assert response.payment_id == existing.id
    assert response.checkout_url == "https://www.asaas.com/i/pay_123"
    assert gateway.create_payment_called == 0


@pytest.mark.asyncio
async def test_create_payment_marks_operation_for_reconciliation_when_local_save_fails_after_gateway_create():
    gateway = FakeGateway()
    gateway_operation_repo = FakeGatewayOperationRepo()
    service = CreatePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(fail_on_save=True),
        gateway_operation_repo=gateway_operation_repo,
    )

    with pytest.raises(DomainError):
        await service.execute(make_request(), make_customer())

    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.REQUIRES_RECONCILIATION


@pytest.mark.asyncio
async def test_create_payment_retries_existing_failed_operation_without_gateway_reference():
    failed_operation = GatewayOperation(
        operation_name="create_payment",
        dedupe_key="create_payment:neectify_shop:order-123",
        provider=GatewayProvider.ASAAS,
        system=System.NEECTIFY_SHOP,
        request_payload=make_request().model_dump(mode="json"),
    )
    failed_operation.id = uuid4()
    failed_operation.mark_failed("gateway timeout")
    gateway = FakeGateway()
    gateway_operation_repo = FakeGatewayOperationRepo(existing=failed_operation)
    payment_repo = FakePaymentRepo()
    service = CreatePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=payment_repo,
        gateway_operation_repo=gateway_operation_repo,
    )

    response = await service.execute(make_request(), make_customer())

    assert response.checkout_url == "https://www.asaas.com/i/pay_123"
    assert gateway.create_payment_called == 1
    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED
