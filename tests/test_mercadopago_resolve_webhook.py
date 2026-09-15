import pytest

from app.application.dtos.request.webhook import EventType
from app.domain.errors import DomainError
from app.infra.interfaces.asaas_provider import AsaasProvider
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

APPROVED_CHECKOUT_PAYMENT = {
    "id": 123456,
    "status": "approved",
    "date_approved": "2026-09-14T12:10:06.000-03:00",
    "payment_type_id": "bank_transfer",
    "transaction_amount": 72.0,
    "external_reference": "checkout:marketfy:order-123",
}


def notification(topic: str, resource_id: str = "123456") -> dict:
    return {"id": 99, "live_mode": False, "type": topic, "action": f"{topic}.updated", "data": {"id": resource_id}}


async def test_payment_topic_fetches_payment(fake_mp_api):
    api = fake_mp_api({("GET", "/v1/payments/123456"): APPROVED_CHECKOUT_PAYMENT})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("payment"))

    assert payload.event == EventType.CHECKOUT_PAID
    assert api.calls[0]["endpoint"] == "/v1/payments/123456"


async def test_authorized_payment_topic_fetches_invoice_and_payment(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/authorized_payments/7001"): {"id": 7001, "preapproval_id": "2c93", "payment": {"id": 123456, "status": "approved"}},
            ("GET", "/v1/payments/123456"): APPROVED_CHECKOUT_PAYMENT | {"external_reference": "sub_1", "payment_type_id": "credit_card"},
        }
    )

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_authorized_payment", "7001"))

    assert payload.event == EventType.PAYMENT_RECEIVED
    assert payload.details.subscription == "2c93"


async def test_scheduled_invoice_without_payment_is_ignored_without_payment_lookup(fake_mp_api):
    api = fake_mp_api({("GET", "/authorized_payments/7002"): {"id": 7002, "preapproval_id": "2c93", "status": "scheduled"}})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_authorized_payment", "7002"))

    assert payload is None
    assert len(api.calls) == 1


async def test_preapproval_topic_fetches_preapproval(fake_mp_api):
    api = fake_mp_api({("GET", "/preapproval/2c93"): {"id": "2c93", "status": "cancelled"}})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_preapproval", "2c93"))

    assert payload.event == EventType.SUBSCRIPTION_INACTIVATED


async def test_unknown_topic_is_ignored(fake_mp_api):
    api = fake_mp_api({})

    assert await MercadoPagoProvider(api=api).resolve_webhook(notification("topic_claims_integration_wh")) is None
    assert api.calls == []


async def test_notification_without_data_id_is_rejected(fake_mp_api):
    with pytest.raises(ValueError):
        await MercadoPagoProvider(api=fake_mp_api({})).resolve_webhook({"type": "payment", "data": {}})


def test_mercadopago_normalize_webhook_requires_resolution(fake_mp_api):
    with pytest.raises(DomainError):
        MercadoPagoProvider(api=fake_mp_api({})).normalize_webhook(notification("payment"))


async def test_asaas_resolve_webhook_defaults_to_normalize():
    payload = await AsaasProvider().resolve_webhook(
        {"id": "evt-1", "event": "CHECKOUT_PAID", "checkout": {"id": "checkout_123", "status": "PAID", "externalReference": "checkout:marketfy:order-123", "items": []}}
    )

    assert payload.event == EventType.CHECKOUT_PAID
    assert payload.details.id == "checkout_123"
