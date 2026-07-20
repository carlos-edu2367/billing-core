from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.checkout import CheckoutItemDTO, CreateCheckoutDTO
from app.application.interfaces.gateway_provider import CreateCheckoutGatewayResponse
from app.application.use_cases.create_checkout import CreateCheckout
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System
from app.domain.errors import DomainError


class FakeCheckoutGateway:
    def __init__(self):
        self.create_checkout_called = 0
        self.last_kwargs = None

    async def create_checkout(self, **kwargs):
        self.create_checkout_called += 1
        self.last_kwargs = kwargs
        return CreateCheckoutGatewayResponse(
            checkout_id="checkout_123",
            checkout_url="https://sandbox.asaas.com/checkoutSession/show/checkout_123",
            status="ACTIVE",
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


def make_request() -> CreateCheckoutDTO:
    return CreateCheckoutDTO(
        system=System.MARKETFY,
        system_payment_id="order-123",
        description="Pedido 123",
        value=Decimal("72.00"),
        minutes_to_expire=30,
        items=[
            CheckoutItemDTO(
                external_reference="pack-100",
                name="100 creditos",
                description="",
                quantity=1,
                value=Decimal("72.00"),
            )
        ],
        success_url="https://app.test/s",
        cancel_url="https://app.test/c",
        expired_url="https://app.test/e",
        webhook_link="https://hooks.neectify.local/billing/payment",
    )


@pytest.mark.asyncio
async def test_create_checkout_persists_gateway_checkout_and_returns_link():
    gateway = FakeCheckoutGateway()
    payment_repo = FakePaymentRepo()
    operation_repo = FakeGatewayOperationRepo()
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=payment_repo,
        gateway_operation_repo=operation_repo,
    )

    response = await service.execute(make_request(), GatewayProvider.ASAAS)

    assert response.checkout_url == "https://sandbox.asaas.com/checkoutSession/show/checkout_123"
    assert gateway.create_checkout_called == 1
    assert gateway.last_kwargs["billing_types"] == ["PIX", "CREDIT_CARD"]
    assert gateway.last_kwargs["charge_types"] == ["DETACHED"]
    assert gateway.last_kwargs["callback"] == {
        "successUrl": "https://app.test/s",
        "cancelUrl": "https://app.test/c",
        "expiredUrl": "https://app.test/e",
    }
    assert gateway.last_kwargs["items"] == [
        {
            "externalReference": "pack-100",
            "name": "100 creditos",
            "description": "",
            "quantity": 1,
            "value": 72.0,
        }
    ]
    assert payment_repo.saved[0].provider_payment_id == "checkout_123"
    assert payment_repo.saved[0].external_reference == "checkout:marketfy:order-123"
    assert payment_repo.saved[0].payment_type == PaymentType.UNDEFINED
    assert payment_repo.saved[0].payment_status == PaymentStatus.PENDING
    assert operation_repo.saved[-1].dedupe_key == "create_checkout:marketfy:order-123"
    assert operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_create_checkout_reuses_existing_local_payment_without_gateway_call():
    existing = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="checkout_123",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://sandbox.asaas.com/checkoutSession/show/checkout_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference="checkout:marketfy:order-123",
    )
    existing.id = uuid4()
    existing.payment_type = PaymentType.UNDEFINED
    gateway = FakeCheckoutGateway()
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(existing=existing),
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    response = await service.execute(make_request(), GatewayProvider.ASAAS)

    assert response.payment_id == existing.id
    assert response.checkout_url == existing.checkout_link
    assert gateway.create_checkout_called == 0


@pytest.mark.asyncio
async def test_create_checkout_retries_existing_failed_operation_without_gateway_reference():
    failed_operation = GatewayOperation(
        operation_name="create_checkout",
        dedupe_key="create_checkout:marketfy:order-123",
        provider=GatewayProvider.ASAAS,
        system=System.MARKETFY,
        request_payload=make_request().model_dump(mode="json"),
    )
    failed_operation.id = uuid4()
    failed_operation.mark_failed("gateway timeout")
    gateway = FakeCheckoutGateway()
    operation_repo = FakeGatewayOperationRepo(existing=failed_operation)
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=operation_repo,
    )

    response = await service.execute(make_request(), GatewayProvider.ASAAS)

    assert response.checkout_url == "https://sandbox.asaas.com/checkoutSession/show/checkout_123"
    assert gateway.create_checkout_called == 1
    assert operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_create_checkout_rejects_failed_operation_with_gateway_reference():
    failed_operation = GatewayOperation(
        operation_name="create_checkout",
        dedupe_key="create_checkout:marketfy:order-123",
        provider=GatewayProvider.ASAAS,
        system=System.MARKETFY,
        request_payload=make_request().model_dump(mode="json"),
        gateway_reference="checkout_123",
    )
    failed_operation.mark_failed("local commit failed")
    gateway = FakeCheckoutGateway()
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=FakeGatewayOperationRepo(existing=failed_operation),
    )

    with pytest.raises(DomainError):
        await service.execute(make_request(), GatewayProvider.ASAAS)

    assert gateway.create_checkout_called == 0


@pytest.mark.asyncio
async def test_create_checkout_rejects_completed_operation_without_local_payment():
    completed_operation = GatewayOperation(
        operation_name="create_checkout",
        dedupe_key="create_checkout:marketfy:order-123",
        provider=GatewayProvider.ASAAS,
        system=System.MARKETFY,
        request_payload=make_request().model_dump(mode="json"),
    )
    completed_operation.mark_completed("checkout_123")
    gateway = FakeCheckoutGateway()
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=FakeGatewayOperationRepo(existing=completed_operation),
    )

    with pytest.raises(DomainError):
        await service.execute(make_request(), GatewayProvider.ASAAS)

    assert gateway.create_checkout_called == 0


@pytest.mark.asyncio
async def test_create_checkout_marks_operation_for_reconciliation_when_local_save_fails_after_remote_create():
    gateway = FakeCheckoutGateway()
    operation_repo = FakeGatewayOperationRepo()
    service = CreateCheckout(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(fail_on_save=True),
        gateway_operation_repo=operation_repo,
    )

    with pytest.raises(DomainError):
        await service.execute(make_request(), GatewayProvider.ASAAS)

    assert operation_repo.saved[-1].status == GatewayOperationStatus.REQUIRES_RECONCILIATION
