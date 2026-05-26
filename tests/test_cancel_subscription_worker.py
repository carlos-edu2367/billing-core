from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.errors import DomainError
from app.domain.enums.subscription_status import SubscriptionStatus
from app.workers import tasks


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
    result = SimpleNamespace(
        model_dump=lambda mode="json": {
            "subscription_id": str(uuid4()),
            "subscription_status": SubscriptionStatus.CANCELED.value,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CancelSubscription", lambda **kwargs: FakeService(result=result))

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
