import json
from datetime import date, timedelta

from app.domain.enums.system import System
from app.infra.config import settings


def subscription_payload():
    next_due = (date.today() + timedelta(days=30)).isoformat()
    expires_at = (date.today() + timedelta(days=395)).isoformat() + "T00:00:00Z"
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


def auth_headers():
    return {
        "X-System": System.NEECTIFY_SHOP.value,
        "X-API-Key": "fake-neectify-shop-key",
    }


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


def test_webhook_rejects_duplicate_payload_in_replay_window(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1"}}
    headers = {"asaas-access-token": "fake-asaas-webhook-secret", "content-type": "application/json"}

    first = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)
    second = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_webhook_requires_json_content_type(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1"}}
    headers = {"asaas-access-token": "fake-asaas-webhook-secret", "content-type": "text/plain"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_webhook_requires_identifiable_event(client):
    payload = {"event": "PAYMENT_RECEIVED", "payment": {}}
    headers = {"asaas-access-token": "fake-asaas-webhook-secret", "content-type": "application/json"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_webhook_rejects_payload_above_size_limit(client):
    original_limit = settings.MAX_WEBHOOK_BODY_BYTES
    settings.MAX_WEBHOOK_BODY_BYTES = 32
    try:
        payload = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay-1", "description": "x" * 128}}
        headers = {"asaas-access-token": "fake-asaas-webhook-secret", "content-type": "application/json"}
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
    """Eventos desconhecidos do Asaas (ex: split) devem retornar 202, não 400."""
    payload = {
        "event": "SUBSCRIPTION_SPLIT_DIVERGENCE_BLOCK",
        "id": "evt-split-001",
        "subscription": {"id": "sub-xxx"},
    }
    headers = {"asaas-access-token": "fake-asaas-webhook-secret", "content-type": "application/json"}

    response = client.post("/v1/webhooks/asaas", content=json.dumps(payload), headers=headers)

    assert response.status_code == 202


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
