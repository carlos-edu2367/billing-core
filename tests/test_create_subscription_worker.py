from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeCustomerRepo:
    def __init__(self, session):
        self.session = session
        self.asked_for = None

    async def get_by_provider_id(self, provider_id):
        self.asked_for = provider_id
        return SimpleNamespace(id=uuid4(), provider_id=provider_id)


class FakeService:
    def __init__(self, result):
        self.result = result

    async def execute(self, dto, customer):
        return self.result


def _dto_dict():
    return {
        "value": Decimal("129.90"),
        "subscription_type": SubscriptionType.MONTHLY,
        "next_due_date": None,
        "description": "Plano Basico",
        "system": System.NEECTIFY_SHOP,
        "system_sub_id": "sub-1",
        "expires_at": datetime.now(timezone.utc),
        "webhook_link": "https://hooks.neectify.local/billing/subscription",
    }


def _ctx(fake_redis, job_id):
    return {
        "job_id": job_id,
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
    }


@pytest.mark.asyncio
async def test_create_subscription_worker_resolves_the_customer_repository(monkeypatch, fake_redis):
    """Regressao: o worker referenciava CustomerRepositoryINFRA sem importa-lo,
    e toda criacao de assinatura morria com NameError antes de tocar o gateway."""
    subscription_id = uuid4()
    result = SimpleNamespace(
        subscription_id=subscription_id,
        model_dump=lambda mode="json": {"subscription_id": str(subscription_id)},
    )
    created_repos = {}

    def _customer_repo(session):
        repo = FakeCustomerRepo(session)
        created_repos["customer"] = repo
        return repo

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "CustomerRepositoryINFRA", _customer_repo)
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CreateSubscription", lambda **kwargs: FakeService(result))

    response = await tasks.create_subscription_worker(
        _ctx(fake_redis, "job-sub-1"), _dto_dict(), "cus_123", "neectify_shop"
    )

    assert response["status"] == "success"
    assert created_repos["customer"].asked_for == "cus_123"
    assert fake_redis.hashes["billing_core:job_meta:job-sub-1"]["status"] == "completed"
