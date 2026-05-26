import pytest

from app.infra.interfaces.internal_webhook import InternalWebhookProvider


class FakeResponse:
    content = b"{}"
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class FakeClient:
    def __init__(self):
        self.headers = None

    async def post(self, url, json, headers):
        self.headers = headers
        return FakeResponse()


@pytest.mark.asyncio
async def test_internal_webhook_provider_sends_idempotency_headers():
    provider = InternalWebhookProvider()
    fake_client = FakeClient()
    provider.client = fake_client

    await provider.send(
        "https://hooks.neectify.local/billing/payment",
        {"event": "PAYMENT_STATUS_UPDATED"},
        webhook_id="delivery-1",
        event_type="PAYMENT_STATUS_UPDATED",
    )

    assert fake_client.headers["X-Webhook-Id"] == "delivery-1"
    assert fake_client.headers["X-Webhook-Event"] == "PAYMENT_STATUS_UPDATED"
