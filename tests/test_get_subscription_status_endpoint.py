from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.application.interfaces.gateway_provider import SubscriptionStatusResponse
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.infra.db.setup import get_db
from app.web.main import app


def auth_headers():
    return {
        "X-System": System.NEECTIFY_SHOP.value,
        "X-API-Key": "fake-neectify-shop-key",
    }


def make_subscription(system=System.NEECTIFY_SHOP):
    return Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="preapproval_1",
        gateway_provider=GatewayProvider.MERCADOPAGO,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=system,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        id=uuid4(),
        value=Decimal("129.90"),
    )


def override_subscription_lookup(subscription):
    class FakeSubscriptionRepo:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, subscription_id):
            if subscription is None or subscription.id != subscription_id:
                from app.domain.errors import NotFoundError

                raise NotFoundError("Subscription Not Found")
            return subscription

    async def fake_db():
        yield object()

    return FakeSubscriptionRepo, fake_db


def test_get_subscription_status_returns_live_gateway_status(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    async def fake_verify_status(subscription_id):
        return SubscriptionStatusResponse(
            subscription_id=subscription_id,
            status="ACTIVE",
            deleted=False,
            next_due_date=date(2026, 11, 1),
            value=Decimal("129.90"),
            cycle="MONTHLY",
        )

    fake_gateway = SimpleNamespace(verify_status=fake_verify_status)
    monkeypatch.setattr(
        "app.web.routes.subscriptions.GetGatewayInfra",
        lambda: SimpleNamespace(get=lambda gateway=None: fake_gateway),
    )

    response = client.get(f"/v1/subscriptions/{subscription.id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["subscription_id"] == str(subscription.id)
    assert body["gateway_status"] == "ACTIVE"
    assert body["value"] == "129.90"
    assert body["cycle"] == "MONTHLY"
    assert body["next_due_date"] == "2026-11-01"


def test_get_subscription_status_404_for_other_system(client, monkeypatch):
    subscription = make_subscription(system=System.NEECTIFY_FOOD)
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.get(f"/v1/subscriptions/{subscription.id}", headers=auth_headers())

    assert response.status_code == 404


def test_get_subscription_status_requires_authentication(client):
    response = client.get(f"/v1/subscriptions/{uuid4()}")

    assert response.status_code == 401
