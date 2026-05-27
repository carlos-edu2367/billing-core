from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.payment_link import CreatePaymentLinkDTO
from app.application.interfaces.gateway_provider import CreatePaymentLinkGatewayResponse
from app.application.use_cases.create_payment_link import CreatePaymentLink
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class FakePaymentLinkGateway:
    def __init__(self):
        self.create_payment_link_called = 0
        self.last_kwargs = None

    async def create_payment_link(self, **kwargs):
        self.create_payment_link_called += 1
        self.last_kwargs = kwargs
        return CreatePaymentLinkGatewayResponse(
            payment_link_id="pml_123",
            checkout_url="https://www.asaas.com/c/pml_123",
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

    async def get_by_external_reference(self, external_reference: str):
        if self.existing and self.existing.external_reference == external_reference:
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


def make_request() -> CreatePaymentLinkDTO:
    return CreatePaymentLinkDTO(
        value=Decimal("72.00"),
        billing_type=PaymentType.UNDEFINED,
        description="Creditos NF-e - pack_100",
        due_date_limit_days=3,
        system=System.MARKETFY,
        system_payment_id="pack-100",
        webhook_link="https://hooks.neectify.local/billing/payment",
    )


@pytest.mark.asyncio
async def test_create_payment_link_persists_gateway_link_and_returns_checkout_url():
    gateway = FakePaymentLinkGateway()
    payment_repo = FakePaymentRepo()
    operation_repo = FakeGatewayOperationRepo()
    service = CreatePaymentLink(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=payment_repo,
        gateway_operation_repo=operation_repo,
    )

    response = await service.execute(make_request(), GatewayProvider.ASAAS)

    assert response.checkout_url == "https://www.asaas.com/c/pml_123"
    assert response.payment_status.value == "pending"
    assert gateway.create_payment_link_called == 1
    assert gateway.last_kwargs["external_reference"] == "payment:marketfy:pack-100"
    assert gateway.last_kwargs["billing_type"] == PaymentType.UNDEFINED
    assert gateway.last_kwargs["due_date_limit_days"] == 3
    assert payment_repo.saved[0].provider_payment_id == "pml_123"
    assert payment_repo.saved[0].checkout_link == "https://www.asaas.com/c/pml_123"
    assert payment_repo.saved[0].payment_type == PaymentType.UNDEFINED
    assert operation_repo.saved[-1].dedupe_key == "create_payment_link:marketfy:pack-100"
    assert operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_create_payment_link_reuses_existing_local_payment_without_gateway_call():
    existing = Payment.create_standalone_payment(
        description="Creditos NF-e - pack_100",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="pack-100",
        provider_payment_id="pml_123",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://www.asaas.com/c/pml_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference="payment:marketfy:pack-100",
    )
    existing.id = uuid4()
    existing.payment_type = PaymentType.UNDEFINED
    gateway = FakePaymentLinkGateway()
    service = CreatePaymentLink(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(existing=existing),
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    response = await service.execute(make_request(), GatewayProvider.ASAAS)

    assert response.payment_id == existing.id
    assert response.checkout_url == "https://www.asaas.com/c/pml_123"
    assert gateway.create_payment_link_called == 0
