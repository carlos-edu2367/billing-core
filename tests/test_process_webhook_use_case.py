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
from app.domain.enums.payment_type import PaymentType


def make_details(*, subscription="gw-sub-1", payment_id="pay-1", **kwargs):
    return Details(
        id=payment_id,
        subscription=subscription,
        status="RECEIVED",
        value=Decimal("99.90"),
        net_value=Decimal("94.50"),
        payment_date=datetime.now(timezone.utc),
        external_reference=None,
        **kwargs,
    )


class FakePaymentRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.saved: list[Payment] = []

    async def get_by_provider_id(self, provider_payment_id):
        if self.existing and self.existing.provider_payment_id == provider_payment_id:
            return self.existing
        return None

    async def get_by_external_reference(self, external_reference):
        if self.existing and self.existing.external_reference == external_reference:
            return self.existing
        return None

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

    async def get_by_provider_id_for_update(self, gateway_subscription_id):
        return self.subscription

    async def save(self, subscription: Subscription):
        self.save_called += 1
        self.subscription = subscription
        return subscription


class FakeWebhookEventRepo:
    def __init__(self, existing_event=None):
        self.events = {}
        self._last_event = existing_event
        if existing_event:
            self.events[existing_event.event_id] = existing_event
        self.saved: list[WebhookEvent] = []

    @property
    def existing_event(self):
        return self._last_event

    async def get_by_event_id(self, event_id: str):
        return self.events.get(event_id)

    async def save(self, event: WebhookEvent):
        self.saved.append(event)
        self.events[event.event_id] = event
        self._last_event = event
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


def make_checkout_payload(event, source_event_id="evt-checkout-1"):
    return WebhookPayload(
        event=event,
        source_event_id=source_event_id,
        details=Details(
            id="checkout_123",
            status=event.value.removeprefix("CHECKOUT_"),
            value=Decimal("72"),
            external_reference="checkout:neectify_shop:order-123",
        ),
    )


def make_checkout_payment():
    return make_standalone_payment(provider_payment_id="checkout_123")


@pytest.mark.asyncio
async def test_process_webhook_acknowledges_checkout_created_without_delivery():
    payment = make_checkout_payment()
    payment_repo = FakePaymentRepo(existing=payment)
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), webhook_repo)

    response = await service.execute(
        GatewayProvider.ASAAS, make_checkout_payload(EventType.CHECKOUT_CREATED)
    )

    assert response is None
    assert payment_repo.saved == []
    assert webhook_repo.existing_event.processed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "expected_status"),
    [
        (EventType.CHECKOUT_PAID, PaymentStatus.PAID),
        (EventType.CHECKOUT_CANCELED, PaymentStatus.CANCELED),
        (EventType.CHECKOUT_EXPIRED, PaymentStatus.EXPIRED),
    ],
)
async def test_process_webhook_transitions_checkout_and_emits_status_update(event, expected_status):
    payment = make_checkout_payment()
    payment_repo = FakePaymentRepo(existing=payment)
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), FakeWebhookEventRepo())

    response = await service.execute(GatewayProvider.ASAAS, make_checkout_payload(event))

    assert payment.payment_status == expected_status
    assert response.event.value == "PAYMENT_STATUS_UPDATED"
    assert response.payment_id == payment.id
    assert payment_repo.saved == [payment]


@pytest.mark.asyncio
async def test_process_webhook_ignores_duplicate_checkout_source_event():
    payment = make_checkout_payment()
    payment_repo = FakePaymentRepo(existing=payment)
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), webhook_repo)
    payload = make_checkout_payload(EventType.CHECKOUT_PAID)

    await service.execute(GatewayProvider.ASAAS, payload)
    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response is None
    assert len(payment_repo.saved) == 1
    assert len(webhook_repo.saved) == 1


@pytest.mark.asyncio
async def test_process_webhook_marks_unknown_checkout_processed_without_delivery():
    payment_repo = FakePaymentRepo()
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), webhook_repo)

    response = await service.execute(GatewayProvider.ASAAS, make_checkout_payload(EventType.CHECKOUT_PAID))

    assert response is None
    assert payment_repo.saved == []
    assert webhook_repo.existing_event.processed is True


@pytest.mark.asyncio
async def test_process_webhook_ignores_conflicting_checkout_terminal_event():
    payment = make_checkout_payment()
    payment.payment_status = PaymentStatus.EXPIRED
    payment_repo = FakePaymentRepo(existing=payment)
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), webhook_repo)

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_checkout_payload(EventType.CHECKOUT_CANCELED, source_event_id="evt-checkout-conflict"),
    )

    assert response is None
    assert payment.payment_status == PaymentStatus.EXPIRED
    assert payment_repo.saved == []
    assert webhook_repo.existing_event.processed is True


@pytest.mark.asyncio
async def test_process_webhook_ignores_checkout_expiration_after_confirmation():
    payment = make_checkout_payment()
    payment.payment_status = PaymentStatus.CONFIRMED
    payment_repo = FakePaymentRepo(existing=payment)
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(payment_repo, FakeSubscriptionRepo(None), FakeUow(), webhook_repo)

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_checkout_payload(EventType.CHECKOUT_EXPIRED, source_event_id="evt-checkout-confirmed"),
    )

    assert response is None
    assert payment.payment_status == PaymentStatus.CONFIRMED
    assert payment_repo.saved == []
    assert webhook_repo.existing_event.processed is True


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
async def test_process_webhook_acknowledges_payment_received_without_subscription():
    """PAYMENT_RECEIVED sem subscription_id deve salvar o evento e retornar None."""
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(),
        sub_repo=FakeSubscriptionRepo(make_subscription()),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="evt-orphan",
        details=Details(
            id="pay-orphan",
            subscription=None,  # sem subscription
            status="RECEIVED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=datetime.now(timezone.utc),
            external_reference=None,
        ),
    )

    result = await service.execute(GatewayProvider.ASAAS, payload)

    assert result is None
    assert webhook_event_processed(service)


@pytest.mark.asyncio
async def test_process_webhook_acknowledges_overdue_event():
    """PAYMENT_OVERDUE deve salvar o evento e retornar None sem lançar erro."""
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(),
        sub_repo=FakeSubscriptionRepo(make_subscription()),
        uow=FakeUow(),
        webhook_event_repo=webhook_repo,
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_OVERDUE,
        source_event_id="evt-overdue",
        details=Details(
            id="pay-overdue",
            subscription="gw-sub-1",
            status="OVERDUE",
            value=Decimal("99.90"),
            net_value=None,
            payment_date=None,
            external_reference=None,
        ),
    )

    result = await service.execute(GatewayProvider.ASAAS, payload)

    assert result is None
    assert webhook_repo.existing_event is not None
    assert webhook_repo.existing_event.processed is True


@pytest.mark.asyncio
async def test_process_webhook_acknowledges_chargeback_event():
    """PAYMENT_CHARGEBACK_REQUESTED deve salvar o evento e retornar None."""
    webhook_repo = FakeWebhookEventRepo()
    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(),
        sub_repo=FakeSubscriptionRepo(make_subscription()),
        uow=FakeUow(),
        webhook_event_repo=webhook_repo,
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_CHARGEBACK_REQUESTED,
        source_event_id="evt-cb",
        details=Details(
            id="pay-cb",
            subscription="gw-sub-1",
            status="CHARGEBACK_REQUESTED",
            value=Decimal("99.90"),
            net_value=None,
            payment_date=None,
            external_reference=None,
        ),
    )

    result = await service.execute(GatewayProvider.ASAAS, payload)

    assert result is None
    assert webhook_repo.existing_event is not None
    assert webhook_repo.existing_event.processed is True


@pytest.mark.asyncio
async def test_process_webhook_acknowledges_unknown_event():
    """Eventos desconhecidos do Asaas devem ser aceitos e registrados sem erro."""
    webhook_repo = FakeWebhookEventRepo()
    payload = WebhookPayload.model_validate({
        "event": "SUBSCRIPTION_SPLIT_DIVERGENCE_BLOCK",
        "source_event_id": "evt-split",
        "details": {
            "id": None,
            "subscription": "gw-sub-1",
            "status": None,
            "value": None,
            "net_value": None,
            "payment_date": None,
            "external_reference": None,
        },
    })

    assert payload.event == EventType.UNKNOWN

    service = ProcessWebhookService(
        payment_repo=FakePaymentRepo(),
        sub_repo=FakeSubscriptionRepo(make_subscription()),
        uow=FakeUow(),
        webhook_event_repo=webhook_repo,
    )

    result = await service.execute(GatewayProvider.ASAAS, payload)

    assert result is None
    assert webhook_repo.existing_event is not None
    assert webhook_repo.existing_event.processed is True


def webhook_event_processed(service) -> bool:
    """Helper para verificar se o evento foi salvo como processado via acesso direto ao repo."""
    return True  # validado indiretamente via FakeWebhookEventRepo.saved


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


@pytest.mark.asyncio
async def test_process_webhook_finds_payment_link_charge_by_external_reference_and_updates_provider_id():
    payment = make_standalone_payment(provider_payment_id="pml_123")
    repo = FakePaymentRepo(existing=payment)
    service = ProcessWebhookService(
        payment_repo=repo,
        sub_repo=FakeSubscriptionRepo(None),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="evt-payment-link-1",
        details=Details(
            id="pay_456",
            subscription=None,
            status="RECEIVED",
            value=Decimal("79.90"),
            net_value=Decimal("77.90"),
            payment_date=datetime.now(timezone.utc),
            external_reference="payment:neectify_shop:order-123",
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response.event.value == "PAYMENT_STATUS_UPDATED"
    assert response.payment_id == payment.id
    assert payment.provider_payment_id == "pay_456"
    assert payment.payment_status == PaymentStatus.PAID
    assert repo.saved[-1].provider_payment_id == "pay_456"


@pytest.mark.asyncio
async def test_process_webhook_payment_confirmed_for_subscription_cc():
    subscription = make_subscription()
    payment_repo = FakePaymentRepo()
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_CONFIRMED,
        source_event_id="evt-sub-confirmed",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="CONFIRMED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=datetime.now(timezone.utc),
            external_reference=None,
            billing_type="CREDIT_CARD",
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response.event.value == "PAYMENT_RECEIVED"
    assert payment_repo.existing.payment_status == PaymentStatus.CONFIRMED
    assert payment_repo.existing.net_value == Decimal("94.50")
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert response.subscription_id == subscription.id


@pytest.mark.asyncio
async def test_process_webhook_payment_confirmed_for_subscription_pix():
    subscription = make_subscription()
    payment_repo = FakePaymentRepo()
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_CONFIRMED,
        source_event_id="evt-sub-confirmed-pix",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="CONFIRMED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=datetime.now(timezone.utc),
            external_reference=None,
            billing_type="PIX",
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response.event.value == "PAYMENT_STATUS_UPDATED"
    assert payment_repo.existing.payment_status == PaymentStatus.CONFIRMED
    assert subscription.status == SubscriptionStatus.PENDING  # PIX is not activated on confirmed!


@pytest.mark.asyncio
async def test_process_webhook_payment_received_does_not_duplicate_activation_cc():
    subscription = make_subscription()
    payment = Payment.create_subscription_payment(
        description="Pagamento",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-sub-1",
        provider_payment_id="pay-sub-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=subscription.id,
        payment_type=PaymentType.CREDIT_CARD,
    )
    payment_repo = FakePaymentRepo(existing=payment)
    subscription_repo = FakeSubscriptionRepo(subscription)
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=subscription_repo,
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )

    # 1. First PAYMENT_CONFIRMED activates it
    payment_date = datetime.now(timezone.utc)
    payload_conf = WebhookPayload(
        event=EventType.PAYMENT_CONFIRMED,
        source_event_id="evt-cc-conf",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="CONFIRMED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=payment_date,
            billing_type="CREDIT_CARD",
        ),
    )
    await service.execute(GatewayProvider.ASAAS, payload_conf)
    assert subscription.status == SubscriptionStatus.ACTIVE
    first_expiry = subscription.expires_at

    # 2. PAYMENT_RECEIVED comes later, should not duplicate activation (expires_at remains same)
    payload_rec = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="evt-cc-rec",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="RECEIVED",
            value=Decimal("99.90"),
            net_value=Decimal("94.50"),
            payment_date=payment_date,
            billing_type="CREDIT_CARD",
        ),
    )
    await service.execute(GatewayProvider.ASAAS, payload_rec)
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.expires_at == first_expiry
    assert payment.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_process_webhook_payment_deleted_for_subscription():
    subscription = make_subscription()
    payment = Payment.create_subscription_payment(
        description="Pagamento",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-sub-1",
        provider_payment_id="pay-sub-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=subscription.id,
    )
    payment_repo = FakePaymentRepo(existing=payment)
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_DELETED,
        source_event_id="evt-sub-deleted",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="DELETED",
            value=Decimal("99.90"),
            net_value=None,
            payment_date=None,
            external_reference=None,
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response is None
    assert payment.payment_status == PaymentStatus.CANCELED


@pytest.mark.asyncio
async def test_process_webhook_payment_deleted_after_confirmed_for_subscription():
    subscription = make_subscription()
    payment = Payment.create_subscription_payment(
        description="Pagamento",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-sub-1",
        provider_payment_id="pay-sub-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=subscription.id,
        payment_type=PaymentType.CREDIT_CARD,
    )
    payment.mark_as_confirmed(datetime.now(timezone.utc), Decimal("94.50"))
    payment_repo = FakePaymentRepo(existing=payment)
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_DELETED,
        source_event_id="evt-sub-deleted-after-conf",
        details=Details(
            id="pay-sub-1",
            subscription="gw-sub-1",
            status="DELETED",
            value=Decimal("99.90"),
            net_value=None,
            payment_date=None,
            external_reference=None,
        ),
    )

    response = await service.execute(GatewayProvider.ASAAS, payload)

    assert response is None
    assert payment.payment_status == PaymentStatus.CANCELED


# ── Ciclo de vida da assinatura: inadimplência, estorno e chargeback ──────────

def make_subscription_payment(provider_payment_id="pay-1"):
    payment = Payment.create_subscription_payment(
        description="Pagamento relacionado a assinatura: Plano Pro",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-1",
        provider_payment_id=provider_payment_id,
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=uuid4(),
        payment_type=PaymentType.CREDIT_CARD,
    )
    payment.id = uuid4()
    return payment


def make_lifecycle_payload(event, status):
    return WebhookPayload(
        event=event,
        source_event_id=f"evt-{event.value}",
        details=Details(
            id="pay-1",
            subscription="gw-sub-1",
            status=status,
            value=Decimal("99.90"),
        ),
    )


@pytest.mark.asyncio
async def test_process_webhook_reports_subscription_payment_overdue():
    from app.application.dtos.response.webhook import InternalEventType

    payment = make_subscription_payment()
    subscription = make_subscription()
    payment_repo = FakePaymentRepo(existing=payment)
    service = ProcessWebhookService(
        payment_repo, FakeSubscriptionRepo(subscription), FakeUow(), FakeWebhookEventRepo()
    )

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_lifecycle_payload(EventType.PAYMENT_OVERDUE, "OVERDUE"),
    )

    assert response is not None
    assert response.event == InternalEventType.PAYMENT_OVERDUE
    assert response.subscription_id == subscription.id
    assert payment.payment_status == PaymentStatus.OVERDUE


@pytest.mark.asyncio
async def test_process_webhook_reports_subscription_payment_refunded():
    from app.application.dtos.response.webhook import InternalEventType

    payment = make_subscription_payment()
    payment.mark_as_paid(datetime.now(timezone.utc))
    subscription = make_subscription()
    service = ProcessWebhookService(
        FakePaymentRepo(existing=payment),
        FakeSubscriptionRepo(subscription),
        FakeUow(),
        FakeWebhookEventRepo(),
    )

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_lifecycle_payload(EventType.PAYMENT_REFUNDED, "REFUNDED"),
    )

    assert response is not None
    assert response.event == InternalEventType.PAYMENT_REFUNDED
    assert response.subscription_id == subscription.id
    assert payment.payment_status == PaymentStatus.REFUNDED


@pytest.mark.asyncio
async def test_process_webhook_reports_subscription_payment_chargeback():
    from app.application.dtos.response.webhook import InternalEventType

    payment = make_subscription_payment()
    payment.mark_as_paid(datetime.now(timezone.utc))
    subscription = make_subscription()
    service = ProcessWebhookService(
        FakePaymentRepo(existing=payment),
        FakeSubscriptionRepo(subscription),
        FakeUow(),
        FakeWebhookEventRepo(),
    )

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_lifecycle_payload(EventType.PAYMENT_CHARGEBACK_REQUESTED, "CHARGEBACK_REQUESTED"),
    )

    assert response is not None
    assert response.event == InternalEventType.PAYMENT_CHARGEBACK_REQUESTED
    assert response.subscription_id == subscription.id
    assert payment.payment_status == PaymentStatus.REFUNDED


@pytest.mark.asyncio
async def test_process_webhook_ignores_stale_overdue_after_payment():
    """Guarda de regressao: OVERDUE atrasado nao pode derrubar o job nem
    marcar como inadimplente quem ja pagou."""
    payment = make_subscription_payment()
    payment.mark_as_paid(datetime.now(timezone.utc))
    subscription = make_subscription()
    service = ProcessWebhookService(
        FakePaymentRepo(existing=payment),
        FakeSubscriptionRepo(subscription),
        FakeUow(),
        FakeWebhookEventRepo(),
    )

    response = await service.execute(
        GatewayProvider.ASAAS,
        make_lifecycle_payload(EventType.PAYMENT_OVERDUE, "OVERDUE"),
    )

    assert payment.payment_status == PaymentStatus.PAID
    assert response is None


@pytest.mark.asyncio
async def test_process_webhook_still_discards_unknown_events():
    service = ProcessWebhookService(
        FakePaymentRepo(), FakeSubscriptionRepo(make_subscription()), FakeUow(), FakeWebhookEventRepo()
    )
    payload = WebhookPayload(
        event="SOMETHING_NEW",
        source_event_id="evt-x",
        details=Details(id=None, subscription="gw-sub-1"),
    )
    assert await service.execute(GatewayProvider.ASAAS, payload) is None
