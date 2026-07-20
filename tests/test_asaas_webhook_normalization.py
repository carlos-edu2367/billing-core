from app.application.dtos.request.webhook import WebhookPayload
from app.infra.interfaces.asaas_provider import AsaasProvider
from decimal import Decimal


def make_asaas_payment_link_webhook():
    return {
        "id": "evt_d26e303b238e509335ac9ba210e51b0f&1351152073",
        "event": "PAYMENT_RECEIVED",
        "payment": {
            "id": "pay_gq4ks2z4kyqncpqy",
            "paymentLink": "dk2qlgdnemzy7nb3",
            "value": 5.0,
            "netValue": 4.01,
            "billingType": "PIX",
            "status": "RECEIVED",
            "paymentDate": "2026-05-27",
            "externalReference": "payment:marketfy:afdaa006-7751-4d50-9b5d-65e062e59f15",
        },
    }


def test_asaas_payment_link_webhook_normalizes_without_subscription():
    payload = AsaasProvider().normalize_webhook(make_asaas_payment_link_webhook())

    assert payload.event.value == "PAYMENT_RECEIVED"
    assert payload.source_event_id == "evt_d26e303b238e509335ac9ba210e51b0f&1351152073"
    assert payload.details.id == "pay_gq4ks2z4kyqncpqy"
    assert payload.details.subscription is None
    assert payload.details.external_reference == "payment:marketfy:afdaa006-7751-4d50-9b5d-65e062e59f15"


def test_normalized_webhook_payload_accepts_missing_optional_detail_keys():
    payload = WebhookPayload.model_validate(
        {
            "event": "PAYMENT_RECEIVED",
            "source_event_id": "evt-1",
            "details": {
                "id": "pay_gq4ks2z4kyqncpqy",
                "status": "RECEIVED",
                "value": 5.0,
                "net_value": 4.01,
                "payment_date": "2026-05-27",
                "external_reference": "payment:marketfy:afdaa006-7751-4d50-9b5d-65e062e59f15",
            },
        }
    )

    assert payload.details.subscription is None


def test_normalized_webhook_payload_accepts_iso_datetime_payment_date_with_z():
    payload = WebhookPayload.model_validate(
        {
            "event": "PAYMENT_RECEIVED",
            "source_event_id": "evt-1",
            "details": {
                "id": "pay_gq4ks2z4kyqncpqy",
                "subscription": None,
                "status": "RECEIVED",
                "value": 5.0,
                "net_value": 4.01,
                "payment_date": "2026-05-27T00:00:00Z",
                "external_reference": "payment:marketfy:afdaa006-7751-4d50-9b5d-65e062e59f15",
            },
        }
    )

    assert payload.details.payment_date.isoformat() == "2026-05-27T00:00:00+00:00"


def test_asaas_checkout_webhook_normalizes_checkout_details_and_item_value():
    payload = AsaasProvider().normalize_webhook(
        {
            "id": "evt-checkout-1",
            "event": "CHECKOUT_PAID",
            "checkout": {
                "id": "checkout_123",
                "status": "PAID",
                "externalReference": "checkout:marketfy:order-123",
                "items": [{"quantity": 1, "value": 72}],
            },
        }
    )

    assert payload.event.value == "CHECKOUT_PAID"
    assert payload.source_event_id == "evt-checkout-1"
    assert payload.details.id == "checkout_123"
    assert payload.details.external_reference == "checkout:marketfy:order-123"
    assert payload.details.value == Decimal("72")
