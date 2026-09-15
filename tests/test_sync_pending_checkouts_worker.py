from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_payment() -> Payment:
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="order-123",
        provider_payment_id="pref-1",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://mp/pref-1",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference="checkout:marketfy:order-123",
    )
    payment.id = uuid4()
    return payment


async def test_sync_worker_expires_checkout_and_enqueues_internal_delivery(monkeypatch, fake_redis):
    pending = make_payment()
    listed_gateways = []
    saved_deliveries = []

    class FakePaymentRepo:
        def __init__(self, session):
            pass

        async def list_pending_checkouts(self, gateway, limit):
            listed_gateways.append(gateway)
            return [pending]

    class FakeDeliveryRepo:
        def __init__(self, session):
            pass

        async def get_by_dedupe_key(self, dedupe_key):
            return None

        async def save(self, delivery):
            delivery.id = uuid4()
            saved_deliveries.append(delivery)
            return delivery

    class FakeSync:
        async def execute(self, payment):
            payment.mark_as_expired()
            return payment

    async def _noop():
        return None

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", FakePaymentRepo)
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", FakeDeliveryRepo)
    monkeypatch.setattr(tasks, "UowProvider", lambda session: SimpleNamespace(commit=_noop, rollback=_noop))
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "SyncCheckoutStatus", lambda **kwargs: FakeSync())

    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    response = await tasks.sync_pending_checkouts_worker(ctx)

    assert response == {"status": "success", "updated": 1}
    assert listed_gateways == [GatewayProvider.MERCADOPAGO]
    assert saved_deliveries[0].payload["payment_status"] == "expired"
    delivery_jobs = [args for args, _ in fake_redis.enqueued_jobs if args[0] == "workers:tasks.send_internal_webhook"]
    assert len(delivery_jobs) == 1
    assert "billing_core:sync_checkouts_lock" not in fake_redis.values


async def test_sync_worker_skips_when_lock_is_held(fake_redis):
    fake_redis.values["billing_core:sync_checkouts_lock"] = "locked"
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    assert await tasks.sync_pending_checkouts_worker(ctx) == {"status": "skipped", "reason": "lock_held"}
