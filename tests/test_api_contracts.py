import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from arq.jobs import serialize_result
import pytest
from pydantic import ValidationError

from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.infra.config import settings
from app.infra.config import InternalApiClientConfig
from app.infra.db.setup import get_db
from app.web.main import app
from app.web.schemas.payment import CreatePaymentRequest


@pytest.fixture(autouse=True)
def configured_checkout_redirect_hosts(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", ["app.neectify.local"], raising=False)


def subscription_payload():
    next_due = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    expires_at = (datetime.now(timezone.utc).date() + timedelta(days=395)).isoformat() + "T00:00:00Z"
    return {
        "customer_provider_id": "cus_123",
        "value": "129.90",
        "subscription_type": "MONTHLY",
        "next_due_date": next_due,
        "description": "Plano Pro anualizado",
        "system": "neectify_shop",
        "system_sub_id": "sub_shop_001",
        "expires_at": expires_at,
        "webhook_link": "https://hooks.neectify.local/billing/subscription",
    }


def checkout_payload(**overrides):
    payload = {
        "system": "neectify_shop",
        "system_payment_id": "order-123",
        "description": "Pacote de créditos",
        "value": "72.00",
        "minutes_to_expire": 30,
        "items": [
            {
                "external_reference": "pack-100",
                "name": "100 créditos",
                "description": "Créditos fiscais",
                "quantity": 1,
                "value": "72.00",
            }
        ],
        "success_url": "https://app.neectify.local/billing/success",
        "cancel_url": "https://app.neectify.local/billing/cancel",
        "expired_url": "https://app.neectify.local/billing/expired",
        "webhook_link": "https://hooks.neectify.local/billing/payment",
    }
    payload.update(overrides)
    return payload


def auth_headers():
    return {
        "X-System": System.NEECTIFY_SHOP.value,
        "X-API-Key": "fake-neectify-shop-key",
    }


def make_subscription(system=System.NEECTIFY_SHOP, status=SubscriptionStatus.ACTIVE):
    return Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=status,
        last_paid_date=None,
        from_system=system,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        id=uuid4(),
        value=Decimal("99.90"),
    )


def make_payment(system=System.NEECTIFY_SHOP):
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=system,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=datetime(2026, 6, 10, tzinfo=timezone.utc).date(),
        external_reference=f"payment:{system.value}:order-123",
    )
    payment.id = uuid4()
    payment.payment_type = PaymentType.UNDEFINED
    return payment


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


def override_payment_lookup(payment):
    class FakePaymentRepo:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, payment_id):
            if payment is None or payment.id != payment_id:
                from app.domain.errors import NotFoundError

                raise NotFoundError("Payment Not Found")
            return payment

    async def fake_db():
        yield object()

    return FakePaymentRepo, fake_db


def test_create_subscription_requires_idempotency_key(client):
    response = client.post(
        "/v1/subscriptions",
        json=subscription_payload(),
        headers=auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_subscription_is_idempotent_per_key_and_payload(client):
    headers = auth_headers() | {"Idempotency-Key": "idem-1"}

    first = client.post("/v1/subscriptions", json=subscription_payload(), headers=headers)
    second = client.post("/v1/subscriptions", json=subscription_payload(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert "ja recebida anteriormente" in second.json()["message"]


def test_create_payment_requires_idempotency_key(client):
    response = client.post(
        "/v1/payments",
        json=checkout_payload(),
        headers=auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_checkout_enqueues_checkout_worker_without_legacy_customer(client, fake_redis):
    response = client.post(
        "/v1/payments",
        json=checkout_payload(),
        headers=auth_headers() | {"Idempotency-Key": "checkout-idem-1"},
    )

    assert response.status_code == 202
    assert fake_redis.enqueued_jobs[0][0][0] == "workers:tasks.create_checkout_worker"
    assert "customer_provider_id" not in fake_redis.enqueued_jobs[0][0][1]
    assert len(fake_redis.enqueued_jobs[0][0]) == 2
    assert fake_redis.hashes[f"billing_core:job_meta:{response.json()['job_id']}"]["job_name"] == "create_checkout_worker"


def test_payment_checkout_rejects_mismatched_total():
    with pytest.raises(ValidationError, match="value deve ser igual"):
        CreatePaymentRequest.model_validate(checkout_payload(value="71.99"))


@pytest.mark.parametrize("minutes", [9, 1441])
def test_payment_checkout_rejects_invalid_expiration(minutes):
    with pytest.raises(ValidationError) as error:
        CreatePaymentRequest.model_validate(checkout_payload(minutes_to_expire=minutes))

    assert any(item["loc"] == ("minutes_to_expire",) for item in error.value.errors())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("items", []),
        ("items", [{"external_reference": "pack-100", "name": "100 créditos", "quantity": 0, "value": "72.00"}]),
        ("items", [{"external_reference": "pack-100", "name": "100 créditos", "quantity": 1, "value": "0"}]),
    ],
)
def test_payment_checkout_rejects_empty_or_invalid_items(field, value):
    with pytest.raises(ValidationError) as error:
        CreatePaymentRequest.model_validate(checkout_payload(**{field: value}))

    assert any(item["loc"][0] == "items" for item in error.value.errors())


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("success_url", "http://app.neectify.local/billing/success"),
        ("cancel_url", "https://untrusted.neectify.local/billing/cancel"),
        ("expired_url", "http://app.neectify.local/billing/expired"),
        ("webhook_link", "http://hooks.neectify.local/billing/payment"),
        ("webhook_link", "https://untrusted.neectify.local/billing/payment"),
    ],
)
def test_payment_checkout_rejects_untrusted_callback_urls(field, url):
    with pytest.raises(ValidationError) as error:
        CreatePaymentRequest.model_validate(checkout_payload(**{field: url}))

    assert any(item["loc"] == (field,) for item in error.value.errors())


@pytest.mark.parametrize("legacy_field", ["customer_provider_id", "due_date", "billing_type"])
def test_payment_checkout_rejects_legacy_fields_with_422(client, legacy_field):
    legacy_value = {"customer_provider_id": "cus_123", "due_date": "2026-06-10", "billing_type": "UNDEFINED"}[legacy_field]
    payload = checkout_payload(**{legacy_field: legacy_value})
    with pytest.raises(ValidationError) as error:
        CreatePaymentRequest.model_validate(payload)

    assert any(item["loc"] == (legacy_field,) and item["type"] == "extra_forbidden" for item in error.value.errors())

    response = client.post(
        "/v1/payments",
        json=payload,
        headers=auth_headers() | {"Idempotency-Key": f"legacy-{legacy_field}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_payment_checkout_rejects_system_different_from_authenticated_system(client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", ["app.neectify.local"], raising=False)

    response = client.post(
        "/v1/payments",
        json=checkout_payload(system="marketfy"),
        headers=auth_headers() | {"Idempotency-Key": "checkout-foreign-system"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_payment_checkout_strips_item_text_and_serializes_worker_payload(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", ["app.neectify.local"], raising=False)
    checkout = CreatePaymentRequest.model_validate(
        checkout_payload(
            items=[
                {
                    "external_reference": " pack-100 ",
                    "name": " 100 créditos ",
                    "description": " Créditos fiscais ",
                    "quantity": 1,
                    "value": "72.00",
                }
            ]
        )
    )

    assert checkout.items[0].external_reference == "pack-100"
    assert checkout.items[0].name == "100 créditos"
    assert checkout.items[0].description == "Créditos fiscais"
    assert checkout.to_worker_payload()["system"] == "neectify_shop"


def test_payment_checkout_rejects_external_reference_above_200_characters(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", ["app.neectify.local"], raising=False)
    system_payment_id = "x" * 180

    with pytest.raises(ValidationError, match="200 caracteres"):
        CreatePaymentRequest.model_validate(checkout_payload(system_payment_id=system_payment_id))


def test_payment_checkout_requires_redirect_hosts_configuration(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", [], raising=False)

    with pytest.raises(ValidationError, match="redirect"):
        CreatePaymentRequest.model_validate(checkout_payload())


def test_payment_checkout_requires_redirect_hosts_in_production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ASAAS_BASE_URL", "https://api.asaas.com/v3")
    monkeypatch.setattr(settings, "ALLOWED_CHECKOUT_REDIRECT_HOSTS", [], raising=False)
    monkeypatch.delitem(settings.__dict__, "resolved_asaas_base_url", raising=False)

    with pytest.raises(RuntimeError, match="ALLOWED_CHECKOUT_REDIRECT_HOSTS"):
        settings.validate_runtime()


def test_payment_links_route_is_removed(client):
    response = client.post(
        "/v1/payment-links",
        json={},
        headers=auth_headers(),
    )

    assert response.status_code == 404


def test_get_job_status_returns_completed_checkout_worker_result(client, fake_redis):
    job_id = "job-checkout-result"
    fake_redis.values[f"billing_core:job_owner:{job_id}"] = System.NEECTIFY_SHOP.value
    fake_redis.hashes[f"billing_core:job_meta:{job_id}"] = {
        "status": "completed",
        "job_name": "create_checkout_worker",
        "attempt": "1",
        "max_tries": "3",
        "request_id": "req-1",
        "created_at": "2026-05-27T12:00:00+00:00",
        "started_at": "2026-05-27T12:00:01+00:00",
        "finished_at": "2026-05-27T12:00:02+00:00",
        "error_code": "",
        "error_message": "",
    }
    fake_redis.values[f"arq:result:{job_id}"] = serialize_result(
        function="workers:tasks.create_checkout_worker",
        args=(),
        kwargs={},
        job_try=1,
        enqueue_time_ms=0,
        success=True,
        result={
            "status": "success",
            "result": {
                "payment_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "checkout_url": "https://www.asaas.com/c/pml_123",
                "payment_status": "pending",
            },
        },
        start_ms=0,
        finished_ms=1,
        ref=job_id,
        queue_name="arq:queue",
        job_id=job_id,
    )

    response = client.get(f"/v1/jobs/{job_id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["checkout_url"] == "https://www.asaas.com/c/pml_123"
    assert body["result"]["payment_id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_payment_polling_enforces_ten_second_interval(client, monkeypatch):
    payment = make_payment()
    fake_repo, fake_db = override_payment_lookup(payment)
    monkeypatch.setattr("app.web.routes.payments.PaymentRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    first = client.get(f"/v1/payments/{payment.id}", headers=auth_headers())
    second = client.get(f"/v1/payments/{payment.id}", headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "10"
    assert second.json()["error"]["code"] == "rate_limit_exceeded"


def test_payment_polling_hides_other_system_payment(client, monkeypatch):
    payment = make_payment(system=System.MARKETFY)
    fake_repo, fake_db = override_payment_lookup(payment)
    monkeypatch.setattr("app.web.routes.payments.PaymentRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.get(f"/v1/payments/{payment.id}", headers=auth_headers())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_cancel_subscription_returns_accepted_job(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    headers = auth_headers() | {"Idempotency-Key": "cancel-idem-1"}
    response = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido do cliente"}, headers=headers)

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-1"


def test_cancel_subscription_requires_authentication(client):
    response = client.post(f"/v1/subscriptions/{uuid4()}/cancel", json={"reason": "pedido"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_cancel_subscription_requires_scope(client, monkeypatch):
    original = settings.INTERNAL_API_CLIENTS
    settings.INTERNAL_API_CLIENTS = {
        "neectify_shop": InternalApiClientConfig(api_key="fake-neectify-shop-key", scopes=["subscriptions:create"])
    }
    try:
        response = client.post(f"/v1/subscriptions/{uuid4()}/cancel", json={"reason": "pedido"}, headers=auth_headers() | {"Idempotency-Key": "cancel-no-scope"})
    finally:
        settings.INTERNAL_API_CLIENTS = original

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_cancel_subscription_returns_not_found_for_missing_subscription(client, monkeypatch):
    fake_repo, fake_db = override_subscription_lookup(None)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.post(f"/v1/subscriptions/{uuid4()}/cancel", json={"reason": "pedido"}, headers=auth_headers() | {"Idempotency-Key": "cancel-missing"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_cancel_subscription_hides_other_system_resources(client, monkeypatch):
    subscription = make_subscription(system=System.MARKETFY)
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido"}, headers=auth_headers() | {"Idempotency-Key": "cancel-foreign"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_cancel_subscription_rejects_already_canceled_subscription(client, monkeypatch, fake_redis):
    subscription = make_subscription(status=SubscriptionStatus.CANCELED)
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido"}, headers=auth_headers() | {"Idempotency-Key": "cancel-canceled"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    idempotency_keys = [key for key in fake_redis.values if key.startswith("billing_core:idempotency:subscription_cancel")]
    assert idempotency_keys == []


def test_cancel_subscription_is_idempotent_per_key_and_payload(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db
    headers = auth_headers() | {"Idempotency-Key": "cancel-idem-2"}

    first = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido"}, headers=headers)
    second = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido"}, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_cancel_subscription_rejects_same_idempotency_key_with_different_payload(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db
    headers = auth_headers() | {"Idempotency-Key": "cancel-idem-3"}

    first = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido 1"}, headers=headers)
    second = client.post(f"/v1/subscriptions/{subscription.id}/cancel", json={"reason": "pedido 2"}, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_cancel_subscription_rejects_large_reason(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db

    response = client.post(
        f"/v1/subscriptions/{subscription.id}/cancel",
        json={"reason": "x" * 501},
        headers=auth_headers() | {"Idempotency-Key": "cancel-large-reason"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_cancel_subscription_rate_limit_blocks_high_frequency_calls(client, monkeypatch):
    subscription = make_subscription()
    fake_repo, fake_db = override_subscription_lookup(subscription)
    monkeypatch.setattr("app.web.routes.subscriptions.SubscriptionRepositoryINFRA", fake_repo)
    app.dependency_overrides[get_db] = fake_db
    responses = []
    for attempt in range(settings.INTERNAL_RATE_LIMIT_REQUESTS + 1):
        responses.append(
            client.post(
                f"/v1/subscriptions/{subscription.id}/cancel",
                json={"reason": "pedido"},
                headers=auth_headers() | {"Idempotency-Key": f"cancel-rate-{attempt}"},
            )
        )

    assert responses[0].status_code == 202
    assert responses[-1].status_code == 429
    assert responses[-1].json()["error"]["code"] == "rate_limit_exceeded"


def test_webhook_rejects_duplicate_payload_in_replay_window(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1"}}
    headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "application/json"}

    first = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)
    second = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["received"] is True
    assert second.json()["duplicate"] is True


def test_webhook_requires_json_content_type(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1"}}
    headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "text/plain"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_webhook_requires_identifiable_event(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {}}
    headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "application/json"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_webhook_replay_key_is_not_claimed_when_event_is_rejected(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {}}
    headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "application/json"}

    first = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)
    second = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert first.status_code == 400
    assert second.status_code == 400
    assert "duplicate" not in second.json()


def test_webhook_rejects_payload_above_size_limit(client):
    original_limit = settings.MAX_WEBHOOK_BODY_BYTES
    settings.MAX_WEBHOOK_BODY_BYTES = 32
    try:
        payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1", "description": "x" * 128}}
        headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "application/json"}
        response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)
    finally:
        settings.MAX_WEBHOOK_BODY_BYTES = original_limit

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_create_subscription_rejects_past_due_date(client):
    headers = auth_headers() | {"Idempotency-Key": "idem-past-due"}
    payload = subscription_payload() | {"next_due_date": "2020-01-01"}

    response = client.post("/v1/subscriptions", json=payload, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_webhook_accepts_unknown_asaas_event_type(client):
    """Eventos desconhecidos do Asaas (ex: split) devem retornar 200, não 400."""
    payload = {
        "event": "SUBSCRIPTION_SPLIT_DIVERGENCE_BLOCK",
        "id": "evt-split-001",
        "subscription": {"id": "sub-xxx"},
    }
    headers = {"asaas-access-token": settings.ASAAS_WEBHOOK_SECRET, "content-type": "application/json"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 200


def test_readiness_returns_dependency_details(client):
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ready", "degraded"}
    assert "redis" in body["dependencies"]
    assert "database" in body["dependencies"]


def test_metrics_endpoint_requires_auth(client):
    response = client.get("/metrics")

    assert response.status_code == 422 or response.status_code == 401


def test_metrics_endpoint_returns_operational_snapshot_for_authorized_client(client):
    response = client.get("/metrics", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["service_name"] == "Billing Core API"
    assert "counters" in body
    assert "durations_ms" in body


def test_metrics_endpoint_can_be_disabled(client):
    original = settings.ENABLE_METRICS_ENDPOINT
    settings.ENABLE_METRICS_ENDPOINT = False
    try:
        response = client.get("/metrics", headers=auth_headers())
    finally:
        settings.ENABLE_METRICS_ENDPOINT = original

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
