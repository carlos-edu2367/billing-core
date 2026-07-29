from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.subscription import CreateSubscriptionDTO
from app.application.use_cases.create_subscription import CreateSubscription
from app.domain.entities.customer import Customer
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.domain.errors import DomainError
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email


@dataclass
class FakeGatewayPayment:
    payment_id: str
    status: str
    value: Decimal
    invoice_url: str


class FakeGateway:
    def __init__(self, fail_after_create: bool = False):
        self.create_subscription_called = 0
        self.fail_after_create = fail_after_create

    async def create_subscription(self, **kwargs):
        self.create_subscription_called += 1
        return "gw-sub-created"

    async def get_subscription_payment(self, subscription_id: str):
        if self.fail_after_create:
            raise RuntimeError("gateway payment listing failed")

        return [
            FakeGatewayPayment(
                payment_id="pay-1",
                status="pending",
                value=Decimal("129.90"),
                invoice_url="https://checkout.local/pay-1",
            )
        ]


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakeSubscriptionRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.saved: list[Subscription] = []

    async def get_by_system_ref(self, system_sub_id, system):
        return self.existing

    async def save(self, subscription: Subscription):
        if subscription.id is None:
            subscription.id = uuid4()
        self.saved.append(subscription)
        return subscription


class FakePaymentRepo:
    def __init__(self, existing_payments=None):
        self.saved: list[Payment] = []
        self.existing_payments = existing_payments or []

    async def list_by_subscription_id(self, subscription_id):
        return self.existing_payments

    async def save(self, payment: Payment):
        payment.id = uuid4()
        self.saved.append(payment)
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
    customer = Customer(
        nome="Carlos",
        email=Email("carlos@example.com"),
        cpf=CPF("39053344705"),
        provider_customer_id="cus-provider-1",
        system_customer_id="cus-1",
        gateway_provider=GatewayProvider.ASAAS,
        system=System.NEECTIFY_SHOP,
    )
    return customer


def make_request() -> CreateSubscriptionDTO:
    return CreateSubscriptionDTO(
        value=Decimal("129.90"),
        subscription_type=SubscriptionType.MONTHLY,
        next_due_date=None,
        description="Plano Pro",
        system=System.NEECTIFY_SHOP,
        system_sub_id="sub-1",
        expires_at=datetime.now(timezone.utc),
        webhook_link="https://hooks.neectify.local/billing/subscription",
    )


@pytest.mark.asyncio
async def test_create_subscription_reuses_existing_subscription_without_calling_gateway():
    existing_subscription = Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.ACTIVE,
        last_paid_date=datetime.now(timezone.utc),
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        id=uuid4(),
        value=Decimal("129.90"),
    )
    existing_payment = Payment.create_subscription_payment(
        description="Pagamento da assinatura",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-1",
        provider_payment_id="pay-1",
        value=Decimal("129.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=existing_subscription.id,
        checkout_link="https://checkout.local/pay-1",
    )
    existing_payment.id = uuid4()

    gateway = FakeGateway()
    service = CreateSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        repo=FakeSubscriptionRepo(existing=existing_subscription),
        payment_repo=FakePaymentRepo(existing_payments=[existing_payment]),
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    response = await service.execute(make_request(), make_customer())

    assert response.subscription_id == existing_subscription.id
    assert response.payment_id == existing_payment.id
    assert gateway.create_subscription_called == 0


@pytest.mark.asyncio
async def test_create_subscription_persists_subscription_and_payment():
    gateway = FakeGateway()
    uow = FakeUow()
    repo = FakeSubscriptionRepo()
    payment_repo = FakePaymentRepo()
    gateway_operation_repo = FakeGatewayOperationRepo()
    service = CreateSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=uow,
        repo=repo,
        payment_repo=payment_repo,
        gateway_operation_repo=gateway_operation_repo,
    )

    response = await service.execute(make_request(), make_customer())

    assert response.checkout_url == "https://checkout.local/pay-1"
    assert len(repo.saved) == 1
    assert len(payment_repo.saved) == 1
    assert uow.commit_called == 2
    assert gateway.create_subscription_called == 1
    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


def make_failed_operation(gateway_reference: str | None = None) -> GatewayOperation:
    operation = GatewayOperation(
        operation_name="create_subscription",
        dedupe_key="create_subscription:neectify_shop:sub-1",
        provider=GatewayProvider.ASAAS,
        system=System.NEECTIFY_SHOP,
        request_payload={"system_sub_id": "sub-1", "value": "99.90"},
        status=GatewayOperationStatus.FAILED,
        gateway_reference=gateway_reference,
        error_message="falha transitoria",
    )
    operation.id = uuid4()
    return operation


@pytest.mark.asyncio
async def test_create_subscription_retries_failed_operation_without_gateway_reference():
    failed_operation = make_failed_operation()
    gateway = FakeGateway()
    gateway_operation_repo = FakeGatewayOperationRepo(existing=failed_operation)
    service = CreateSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        repo=FakeSubscriptionRepo(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=gateway_operation_repo,
    )

    response = await service.execute(make_request(), make_customer())

    assert response.checkout_url == "https://checkout.local/pay-1"
    assert gateway.create_subscription_called == 1
    assert gateway_operation_repo.saved[-1].id == failed_operation.id
    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_create_subscription_retry_refreshes_stale_request_payload():
    failed_operation = make_failed_operation()
    gateway_operation_repo = FakeGatewayOperationRepo(existing=failed_operation)
    service = CreateSubscription(
        get_gateway=FakeGetGateway(FakeGateway()),
        uow=FakeUow(),
        repo=FakeSubscriptionRepo(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=gateway_operation_repo,
    )

    await service.execute(make_request(), make_customer())

    assert gateway_operation_repo.saved[-1].request_payload["value"] == "129.90"


@pytest.mark.asyncio
async def test_create_subscription_blocks_failed_operation_with_gateway_reference():
    failed_operation = make_failed_operation(gateway_reference="gw-sub-orphan")
    gateway = FakeGateway()
    service = CreateSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        repo=FakeSubscriptionRepo(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=FakeGatewayOperationRepo(existing=failed_operation),
    )

    with pytest.raises(DomainError) as exc_info:
        await service.execute(make_request(), make_customer())

    assert "reconciliacao" in str(exc_info.value).lower()
    assert gateway.create_subscription_called == 0


@pytest.mark.asyncio
async def test_create_subscription_marks_operation_for_reconciliation_when_local_sync_fails():
    gateway = FakeGateway(fail_after_create=True)
    uow = FakeUow()
    gateway_operation_repo = FakeGatewayOperationRepo()
    service = CreateSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=uow,
        repo=FakeSubscriptionRepo(),
        payment_repo=FakePaymentRepo(),
        gateway_operation_repo=gateway_operation_repo,
    )

    with pytest.raises(DomainError) as exc_info:
        await service.execute(make_request(), make_customer())

    assert "reconciliacao" in str(exc_info.value).lower()
    assert uow.rollback_called == 1
    assert gateway_operation_repo.saved[-1].status == GatewayOperationStatus.REQUIRES_RECONCILIATION
