from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.dtos.response.webhook import InternalEventType
from app.domain.errors import DomainError
from app.domain.enums.subscription_status import SubscriptionStatus
from app.workers import tasks


async def _noop(*args, **kwargs):
    return None


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeService:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    async def execute(self, dto):
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_cancel_subscription_worker_completes_successfully(monkeypatch, fake_redis):
    subscription_id = uuid4()
    result = SimpleNamespace(
        subscription_id=subscription_id,
        subscription_status=SubscriptionStatus.CANCELED,
        cancelled_at=datetime.now(timezone.utc),
        model_dump=lambda mode="json": {
            "subscription_id": str(subscription_id),
            "subscription_status": SubscriptionStatus.CANCELED.value,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(result=result))
    monkeypatch.setattr(tasks, "_build_internal_delivery", _noop)

    ctx = {"job_id": "job-1", "job_try": 1, "redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}
    response = await tasks.cancel_subscription_worker(ctx, {"subscription_id": str(uuid4()), "system": "neectify_shop", "reason": "pedido"})

    assert response["status"] == "success"
    assert fake_redis.hashes["billing_core:job_meta:job-1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_subscription_worker_marks_domain_failures_without_retry(monkeypatch, fake_redis):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(exc=DomainError("invalid cancel")))

    ctx = {"job_id": "job-2", "job_try": 1, "redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}
    response = await tasks.cancel_subscription_worker(ctx, {"subscription_id": str(uuid4()), "system": "neectify_shop"})

    assert response["status"] == "failed"
    assert fake_redis.hashes["billing_core:job_meta:job-2"]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancel_subscription_worker_retries_transient_failures(monkeypatch, fake_redis):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(exc=RuntimeError("gateway timeout")))

    ctx = {"job_id": "job-3", "job_try": 1, "redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}

    with pytest.raises(RuntimeError):
        await tasks.cancel_subscription_worker(ctx, {"subscription_id": str(uuid4()), "system": "neectify_shop"})

    assert fake_redis.hashes["billing_core:job_meta:job-3"]["status"] == "retrying"


@pytest.mark.asyncio
async def test_cancel_subscription_worker_notifies_the_consumer_system(monkeypatch, fake_redis):
    """O cancelamento confirmado no gateway precisa gerar entrega interna.

    Sem isso o sistema consumidor (ex.: Neectify Food) nunca sabe que a
    assinatura acabou e mantem o plano pago liberado indefinidamente.
    """
    subscription_id = uuid4()
    delivery_id = uuid4()
    result = SimpleNamespace(
        subscription_id=subscription_id,
        subscription_status=SubscriptionStatus.CANCELED,
        cancelled_at=datetime.now(timezone.utc),
        model_dump=lambda mode="json": {"subscription_id": str(subscription_id)},
    )

    built: list = []

    async def fake_build(res, sub_repo, payment_repo):
        built.append(res)
        return SimpleNamespace(dedupe_key="k", id=delivery_id)

    class FakeDeliveryRepo:
        def __init__(self, session): pass
        async def get_by_dedupe_key(self, key): return None
        async def save(self, delivery): return delivery

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", FakeDeliveryRepo)
    monkeypatch.setattr(tasks, "UowProvider", lambda session: SimpleNamespace(
        commit=_noop, rollback=_noop))
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(result=result))
    monkeypatch.setattr(tasks, "_build_internal_delivery", fake_build)

    ctx = {"job_id": "job-4", "job_try": 1, "redis": fake_redis, "logger": SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}
    response = await tasks.cancel_subscription_worker(
        ctx, {"subscription_id": str(subscription_id), "system": "neectify_shop", "reason": "pedido"}
    )

    assert response["status"] == "success"
    assert len(built) == 1, "nenhuma entrega interna foi construida"
    assert built[0].event is InternalEventType.SUBSCRIPTION_INACTIVATED
    assert built[0].subscription_id == subscription_id
    assert fake_redis.enqueued_jobs, "entrega nao foi enfileirada"
    assert fake_redis.enqueued_jobs[-1][0][0] == "workers:tasks.send_internal_webhook"


@pytest.mark.asyncio
async def test_cancel_subscription_worker_skips_delivery_when_not_canceled(monkeypatch, fake_redis):
    """Cancelamento que nao terminou CANCELED nao deve notificar."""
    subscription_id = uuid4()
    result = SimpleNamespace(
        subscription_id=subscription_id,
        subscription_status=SubscriptionStatus.CANCELLATION_PENDING,
        cancelled_at=None,
        model_dump=lambda mode="json": {"subscription_id": str(subscription_id)},
    )
    built: list = []

    async def fake_build(res, sub_repo, payment_repo):
        built.append(res)
        return None

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: SimpleNamespace(
        commit=_noop, rollback=_noop))
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(result=result))
    monkeypatch.setattr(tasks, "_build_internal_delivery", fake_build)

    ctx = {"job_id": "job-5", "job_try": 1, "redis": fake_redis, "logger": SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}
    await tasks.cancel_subscription_worker(
        ctx, {"subscription_id": str(subscription_id), "system": "neectify_shop"}
    )

    assert built == []
    assert not fake_redis.enqueued_jobs
