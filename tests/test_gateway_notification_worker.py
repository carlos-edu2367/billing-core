from types import SimpleNamespace

import pytest

from app.application.dtos.request.webhook import WebhookPayload
from app.workers import tasks

NOTIFICATION = {"id": 1, "type": "payment", "data": {"id": "123456"}}


def make_ctx(fake_redis):
    return {
        "job_id": "job-mp-1",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }


class FakeGateway:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    async def resolve_webhook(self, payload):
        if self.exc:
            raise self.exc
        return self.result


def patch_gateway(monkeypatch, gateway):
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: SimpleNamespace(get=lambda provider: gateway))


async def test_ignored_notification_completes_without_processing(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(result=None))

    async def fail_process_webhook(*args, **kwargs):
        raise AssertionError("nao deveria processar")

    monkeypatch.setattr(tasks, "process_webhook", fail_process_webhook)

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response == {"status": "ignored", "result": None}


async def test_resolved_notification_is_delegated_to_process_webhook(monkeypatch, fake_redis):
    payload = WebhookPayload.model_validate(
        {"event": "CHECKOUT_PAID", "source_event_id": "payment:123456:approved", "details": {"id": "123456", "external_reference": "checkout:marketfy:1"}}
    )
    patch_gateway(monkeypatch, FakeGateway(result=payload))
    received = {}

    async def fake_process_webhook(ctx, payload_dict, gateway_provider_str):
        received["payload"] = payload_dict
        received["provider"] = gateway_provider_str
        return {"status": "success"}

    monkeypatch.setattr(tasks, "process_webhook", fake_process_webhook)

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response == {"status": "success"}
    assert received["provider"] == "MERCADOPAGO"
    assert received["payload"]["event"] == "CHECKOUT_PAID"


async def test_invalid_notification_is_terminal(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(exc=ValueError("Notificacao do Mercado Pago sem data.id.")))

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response["status"] == "failed"


async def test_unexpected_resolution_error_is_retried(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(exc=RuntimeError("network down")))

    with pytest.raises(RuntimeError):
        await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")
