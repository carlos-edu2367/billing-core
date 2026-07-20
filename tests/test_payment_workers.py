from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.system import System
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeCreateCheckoutService:
    async def execute(self, dto, gateway_provider):
        return SimpleNamespace(
            payment_id=uuid4(),
            checkout_url="https://www.asaas.com/c/pml_123",
            payment_status=PaymentStatus.PENDING,
            model_dump=lambda mode="json": {
                "payment_id": str(uuid4()),
                "checkout_url": "https://www.asaas.com/checkout/checkout_123",
                "payment_status": "pending",
            },
        )


class FakeWebhookPaymentRepo:
    def __init__(self, session):
        self.payment = Payment.create_standalone_payment(
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
        self.payment.id = uuid4()
        self.payment.mark_as_paid()

    async def get_by_id(self, payment_id):
        self.payment.id = payment_id
        return self.payment


class FakeDeliveryRepo:
    saved: list[InternalWebhookDelivery] = []

    def __init__(self, session):
        self.session = session

    async def get_by_dedupe_key(self, dedupe_key):
        return None

    async def save(self, delivery):
        delivery.id = uuid4()
        self.__class__.saved.append(delivery)
        return delivery


class FakeProcessWebhookService:
    async def execute(self, gateway_provider, payload):
        return SimpleNamespace(
            event=tasks.InternalEventType.PAYMENT_STATUS_UPDATED,
            payment_id=uuid4(),
            subscription_id=None,
            model_dump=lambda mode="json": {
                "event": "PAYMENT_STATUS_UPDATED",
                "payment_id": str(uuid4()),
                "subscription_id": None,
            },
        )


@pytest.mark.asyncio
async def test_create_checkout_worker_returns_checkout_url_without_reconciliation(monkeypatch, fake_redis):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CreateCheckout", lambda **kwargs: FakeCreateCheckoutService())

    ctx = {
        "job_id": "job-checkout-1",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }

    response = await tasks.create_checkout_worker(
        ctx,
        {
            "description": "Creditos NF-e - pack_100",
            "value": "72.00",
            "minutes_to_expire": 30,
            "system": "neectify_shop",
            "system_payment_id": "pack-100",
            "webhook_link": "https://hooks.neectify.local/billing/payment",
            "success_url": "https://app.neectify.local/billing/success",
            "cancel_url": "https://app.neectify.local/billing/cancel",
            "expired_url": "https://app.neectify.local/billing/expired",
            "items": [{"external_reference": "pack-100", "name": "100 creditos", "quantity": 1, "value": "72.00"}],
        },
    )

    assert response["status"] == "success"
    assert response["result"]["checkout_url"].startswith("https://")
    reconcile_jobs = [item for item in fake_redis.enqueued_jobs if item[0][0] == "workers:tasks.reconcile_pending_payment_worker"]
    assert reconcile_jobs == []


@pytest.mark.asyncio
async def test_process_webhook_worker_enqueues_internal_delivery_for_standalone_payment(monkeypatch, fake_redis):
    FakeDeliveryRepo.saved = []
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", FakeWebhookPaymentRepo)
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "WebhookEventRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", FakeDeliveryRepo)
    monkeypatch.setattr(tasks, "UowProvider", lambda session: SimpleNamespace(commit=lambda: _async_none()))
    monkeypatch.setattr(tasks, "ProcessWebhookService", lambda **kwargs: FakeProcessWebhookService())

    ctx = {
        "job_id": "job-2",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }

    response = await tasks.process_webhook(
        ctx,
        {
            "event": "PAYMENT_RECEIVED",
            "source_event_id": "evt-1",
            "details": {
                "id": "pay_123",
                "subscription": None,
                "status": "RECEIVED",
                "value": None,
                "net_value": None,
                "payment_date": None,
                "external_reference": None,
            },
        },
        "ASAAS",
    )

    assert response["status"] == "success"
    assert FakeDeliveryRepo.saved[0].subscription_id is None
    assert FakeDeliveryRepo.saved[0].payment_id is not None
    delivery_jobs = [item for item in fake_redis.enqueued_jobs if item[0][0] == "workers:tasks.send_internal_webhook"]
    assert len(delivery_jobs) == 1


async def _async_none():
    return None
