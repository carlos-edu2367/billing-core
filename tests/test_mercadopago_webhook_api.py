import hashlib
import hmac
import json

from app.infra.config import settings

SECRET = "fake-mercadopago-webhook-secret-with-32-chars"


def signed_headers(data_id: str, request_id: str = "req-1", ts: str = "1704908010") -> dict:
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id, "content-type": "application/json"}


def notification(data_id: str = "123456") -> dict:
    return {"id": 12345, "live_mode": False, "type": "payment", "action": "payment.updated", "data": {"id": data_id}}


def test_mercadopago_webhook_enqueues_notification_resolution(client, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)

    response = client.post(
        "/v1/webhooks/mercadopago?data.id=123456&type=payment",
        content=json.dumps(notification()),
        headers=signed_headers("123456"),
    )

    assert response.status_code == 200
    jobs = [args for args, _ in fake_redis.enqueued_jobs if args[0] == "workers:tasks.process_gateway_notification"]
    assert jobs == [("workers:tasks.process_gateway_notification", notification(), "MERCADOPAGO")]


def test_mercadopago_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    headers = signed_headers("123456") | {"x-signature": "ts=1704908010,v1=deadbeef"}

    response = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=json.dumps(notification()), headers=headers)

    assert response.status_code == 401


def test_mercadopago_webhook_marks_replayed_body_as_duplicate(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    body = json.dumps(notification())

    first = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=body, headers=signed_headers("123456"))
    second = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=body, headers=signed_headers("123456"))

    assert first.status_code == 200
    assert second.json() == {"received": True, "duplicate": True}


def test_mercadopago_webhook_requires_data_id_in_body(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    manifest_without_id = "request-id:req-1;ts:1704908010;"
    v1 = hmac.new(SECRET.encode(), manifest_without_id.encode(), hashlib.sha256).hexdigest()
    headers = {"x-signature": f"ts=1704908010,v1={v1}", "x-request-id": "req-1", "content-type": "application/json"}

    response = client.post("/v1/webhooks/mercadopago", content=json.dumps({"type": "payment", "data": {}}), headers=headers)

    assert response.status_code == 400
