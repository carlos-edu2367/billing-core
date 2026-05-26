from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.webhook import Details, EventType, WebhookPayload
from app.application.use_cases.process_webhook import ProcessWebhookService
from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription
from app.domain.entities.webhook_event import WebhookEvent
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System


class FakePaymentRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.saved: list[Payment] = []

    async def get_by_provider_id(self, provider_payment_id):
        return self.existing

    async def save(self, payment: Payment):
        if payment.id is None:
            payment.id = uuid4()
        self.saved.append(payment)
        self.existing = payment
        return payment


class FakeSubscriptionRepo:
    def __init__(self, subscription: Subscription):
        self.subscription = subscription
        self.save_called = 0

    async def get_by_provider_id(self, gateway_subscription_id):
        return self.subscription

    async def save(self, subscription: Subscription):
        self.save_called += 1
        self.subscription = subscription
        return subscription


class FakeWebhookEventRepo:
    def __init__(self, existing_event=None):
        self.existing_event = existing_event
        self.saved: list[WebhookEvent] = []

    async def get_by_event_id(self, event_id: str):
        return self.existing_event

    async def save(self, event: WebhookEvent):
        self.saved.append(event)
        self.existing_event = event
        return event


class FakeUow:
    def __init__(self):
        self.commit_called = 0

    async def commit(self):
        self.commit_called += 1


def make_subscription():
    return Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        id=uuid4(),
        value=Decimal("99.90"),
    )


def make_payload():
    return WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="evt-1",
        details=Details(
            id="pay-1",
            subscription="gw-sub-1",
            status="RECEIVED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=datetime.now(timezone.utc),
            external_reference=None,
        ),
    )


def make_standalone_payment(provider_payment_id="pay-1"):
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id=provider_payment_id,
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=datetime(2026, 6, 10, tzinfo=timezone.utc).date(),
        external_reference="payment:neectify_shop:order-123",
    )
    payment.id = uuid4()
    return payment


@pytest.mark.asyncio
async def test_process_webhook_creates_payment_and_marks_subscription_as_paid():
    subscription = make_subscription()
    payment_repo = FakePaymentRepo()
    subscription_repo = FakeSubscriptionRepo(subscription)
    webhook_repo = FakeWebhookEventRepo()
    uow = FakeUow()
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=subscription_repo,
        uow=uow,
        webhook_event_repo=webhook_repo,
    )

    response = await service.execute(GatewayProvider.ASAAS, make_payload())

    assert response.subscription_id == subscription.id
    assert payment_repo.existing.payment_status == PaymentStatus.PAID
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert webhook_repo.existing_event.processed is True
    assert uow.commit_called == 1


@pytest.mark.asyncio
async def test_process_webhook_returns_none_for_already_processed_event():
    processed_event = WebhookEvent(
        event_id="asaas:PAYMENT_RECEIVED:evt-1:gw-sub-1",
        provider=GatewayProvider.ASAAS,
        event_type=EventType.PAYMENT_RECEIVED.value,
        payload={},
        processed=True,
    )
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(),
        sub_repo=FakeSubscriptionRepo(make_subscription()),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(existing_event=processed_event),
    )

    response = await service.execute(GatewayProvider.ASAAS, make_payload())

    assert response is None


@pytest.mark.asyncio
async def test_process_webhook_marks_standalone_payment_as_paid():
    payment = make_standalone_payment(provider_payment_id="pay-1")
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(existing=payment),
        sub_repo=FakeSubscriptionRepo(None),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="evt-standalone-1",
        details=Details(
            id="pay-1",
            subscription=None,
            status="RECEIVED",
            value=Decimal("79.90"),
            net_value=Decimal("77.90"),
            payment_date=datetime.now(timezone.utc),
            external_reference="payment:neectify_shop:order-123",
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert payment.payment_status == PaymentStatus.PAID
    assert response.event.value == "PAYMENT_STATUS_UPDATED"
    assert response.payment_id == payment.id


@pytest.mark.asyncio
async def test_process_webhook_marks_standalone_payment_as_confirmed():
    payment = make_standalone_payment(provider_payment_id="pay-1")
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(existing=payment),
        sub_repo=FakeSubscriptionRepo(None),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_CONFIRMED,
        source_event_id="evt-standalone-2",
        details=Details(
            id="pay-1",
            subscription=None,
            status="CONFIRMED",
            value=Decimal("79.90"),
            net_value=Decimal("77.90"),
            payment_date=datetime.now(timezone.utc),
            external_reference="payment:neectify_shop:order-123",
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert payment.payment_status == PaymentStatus.CONFIRMED
    assert response.event.value == "PAYMENT_STATUS_UPDATED"
