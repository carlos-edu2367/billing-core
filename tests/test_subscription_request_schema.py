import pytest
from pydantic import ValidationError

from app.web.schemas.subscription import CreateSubscriptionRequest

BASE = {
    "customer_provider_id": "cus_123",
    "value": "129.90",
    "subscription_type": "MONTHLY",
    "description": "Plano Pro",
    "system": "marketfy",
    "system_sub_id": "sub_local_1",
    "expires_at": "2099-01-01T00:00:00Z",
    "webhook_link": "https://hooks.neectify.local/billing/subscription",
}


def test_back_url_is_optional():
    req = CreateSubscriptionRequest.model_validate(BASE)
    assert req.back_url is None


def test_back_url_accepts_allowed_host():
    payload = {**BASE, "back_url": "https://app.neectify.com/billing/retorno?ref=abc"}
    req = CreateSubscriptionRequest.model_validate(payload)
    assert req.back_url == "https://app.neectify.com/billing/retorno?ref=abc"


def test_back_url_rejects_disallowed_host():
    payload = {**BASE, "back_url": "https://evil.example/steal"}
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(payload)


def test_back_url_rejects_non_https():
    payload = {**BASE, "back_url": "http://app.neectify.com/billing/retorno"}
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(payload)
