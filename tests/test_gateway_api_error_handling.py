from types import SimpleNamespace

import pytest

from app.application.interfaces.gateway_provider import GatewayAPIError
from app.infra.interfaces.asaas_provider import AsaasAPIError
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OtherGatewayError(GatewayAPIError):
    provider_label = "Other"


class RaisingCheckoutService:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def execute(self, dto, gateway_provider):
        raise self.exc


CHECKOUT_PAYLOAD = {
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
}


def make_ctx(fake_redis):
    return {
        "job_id": "job-gw-1",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }


def patch_checkout_worker(monkeypatch, exc: Exception):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CreateCheckout", lambda **kwargs: RaisingCheckoutService(exc))


def test_asaas_api_error_keeps_message_and_is_a_gateway_error():
    exc = AsaasAPIError(400, "invalid", "POST", "/checkouts")

    assert isinstance(exc, GatewayAPIError)
    assert str(exc) == "Asaas POST /checkouts → 400: invalid"
    assert exc.is_client_error is True


async def test_checkout_worker_treats_any_gateway_client_error_as_terminal(monkeypatch, fake_redis):
    patch_checkout_worker(monkeypatch, OtherGatewayError(422, "bad request", "POST", "/x"))

    response = await tasks.create_checkout_worker(make_ctx(fake_redis), CHECKOUT_PAYLOAD)

    assert response["status"] == "failed"


async def test_checkout_worker_reraises_gateway_server_error_for_retry(monkeypatch, fake_redis):
    patch_checkout_worker(monkeypatch, OtherGatewayError(503, "unavailable", "POST", "/x"))

    with pytest.raises(OtherGatewayError):
        await tasks.create_checkout_worker(make_ctx(fake_redis), CHECKOUT_PAYLOAD)
