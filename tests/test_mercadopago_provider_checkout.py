from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.errors import DomainError
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
REFERENCE = "checkout:marketfy:order-123"
INIT_POINT = "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"
CALLBACK = {"successUrl": "https://app.test/s", "cancelUrl": "https://app.test/c", "expiredUrl": "https://app.test/e"}
ITEMS = [{"externalReference": "pack-100", "name": "100 creditos", "description": "", "quantity": 1, "value": 72.0}]


def make_provider(api, now=NOW):
    return MercadoPagoProvider(api=api, now=lambda: now)


async def create_checkout(provider, minutes_to_expire=45):
    return await provider.create_checkout(
        billing_types=["PIX", "CREDIT_CARD"],
        charge_types=["DETACHED"],
        minutes_to_expire=minutes_to_expire,
        external_reference=REFERENCE,
        callback=CALLBACK,
        items=ITEMS,
    )


async def test_create_checkout_builds_preference_limited_to_pix_and_card(fake_mp_api):
    api = fake_mp_api({("POST", "/checkout/preferences"): {"id": "pref-1", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    response = await create_checkout(make_provider(api))

    call = api.calls[0]
    payload = call["payload"]
    assert call["idempotency_key"] == REFERENCE
    assert payload["external_reference"] == REFERENCE
    assert payload["items"] == [
        {"id": "pack-100", "title": "100 creditos", "description": "", "quantity": 1, "unit_price": 72.0, "currency_id": "BRL"}
    ]
    assert payload["back_urls"] == {"success": "https://app.test/s", "pending": "https://app.test/s", "failure": "https://app.test/c"}
    assert payload["auto_return"] == "approved"
    assert payload["expires"] is True
    assert payload["expiration_date_from"] == "2026-09-14T12:00:00.000+00:00"
    assert payload["expiration_date_to"] == "2026-09-14T12:45:00.000+00:00"
    assert payload["date_of_expiration"] == payload["expiration_date_to"]
    excluded = payload["payment_methods"]["excluded_payment_types"]
    assert {"id": "bank_transfer"} not in excluded
    assert {"id": "ticket"} in excluded
    assert response.checkout_id == "pref-1"
    assert response.checkout_url == INIT_POINT
    assert response.status == "ACTIVE"
    assert response.external_reference == REFERENCE


async def test_create_checkout_extends_short_expiration_to_pix_minimum(fake_mp_api):
    api = fake_mp_api({("POST", "/checkout/preferences"): {"id": "pref-1", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    await create_checkout(make_provider(api), minutes_to_expire=10)

    assert api.calls[0]["payload"]["expiration_date_to"] == "2026-09-14T12:30:00.000+00:00"


@pytest.mark.parametrize(
    "response",
    [
        {"init_point": INIT_POINT, "external_reference": REFERENCE},
        {"id": "pref-1", "external_reference": REFERENCE},
        {"id": "pref-1", "init_point": INIT_POINT},
        {"id": "pref-1", "init_point": INIT_POINT, "external_reference": "checkout:marketfy:other"},
    ],
)
async def test_create_checkout_rejects_incomplete_or_divergent_response(fake_mp_api, response):
    api = fake_mp_api({("POST", "/checkout/preferences"): response})

    with pytest.raises(DomainError):
        await create_checkout(make_provider(api))


@pytest.mark.parametrize(
    ("results", "now", "expected"),
    [
        ([{"id": 1, "status": "approved"}], NOW, "PAID"),
        ([], NOW, "ACTIVE"),
        ([], NOW + timedelta(hours=1), "EXPIRED"),
    ],
)
async def test_get_checkout_derives_status_from_payments(fake_mp_api, results, now, expected):
    api = fake_mp_api(
        {
            ("GET", "/checkout/preferences/pref-1"): {
                "id": "pref-1",
                "init_point": INIT_POINT,
                "external_reference": REFERENCE,
                "expires": True,
                "expiration_date_to": "2026-09-14T12:30:00.000+00:00",
            },
            ("GET", "/v1/payments/search"): {"paging": {"total": len(results)}, "results": results},
        }
    )

    response = await make_provider(api, now=now).get_checkout("pref-1")

    assert response.status == expected
    assert api.calls[1]["params"] == {"external_reference": REFERENCE, "sort": "date_created", "criteria": "desc"}


async def test_get_checkout_rejects_other_preference(fake_mp_api):
    api = fake_mp_api({("GET", "/checkout/preferences/pref-1"): {"id": "pref-2", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    with pytest.raises(DomainError, match="id divergente"):
        await make_provider(api).get_checkout("pref-1")


async def test_get_payment_maps_status_net_value_and_billing_type(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/payments/123"): {
                "id": 123,
                "status": "approved",
                "transaction_amount": 72.0,
                "transaction_details": {"net_received_amount": 71.28},
                "date_approved": "2026-09-14T12:10:06.000-03:00",
                "payment_type_id": "credit_card",
                "external_reference": REFERENCE,
            }
        }
    )

    response = await make_provider(api).get_payment("123")

    assert response.payment_id == "123"
    assert response.status == "RECEIVED"
    assert response.value == Decimal("72.0")
    assert response.net_value == Decimal("71.28")
    assert response.billing_type == "CREDIT_CARD"
    assert str(response.payment_date) == "2026-09-14"


async def test_create_customer_reuses_customer_found_by_email(fake_mp_api):
    api = fake_mp_api(
        {("GET", "/v1/customers/search"): {"results": [{"id": "1234-abc", "email": "joao@exemplo.com", "first_name": "Joao", "last_name": "Silva"}]}}
    )

    response = await make_provider(api).create_customer(name="Joao Silva", cpfCnpj="39053344705", email="joao@exemplo.com", external_reference="user_42")

    assert response.cus_id == "1234-abc"
    assert [call["method"] for call in api.calls] == ["GET"]


async def test_create_customer_creates_with_document_identification(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/search"): {"results": []},
            ("POST", "/v1/customers"): {"id": "5678-def", "email": "empresa@exemplo.com", "first_name": "Empresa", "last_name": "LTDA"},
        }
    )

    response = await make_provider(api).create_customer(name="Empresa LTDA", cpfCnpj="11222333000181", email="empresa@exemplo.com", external_reference="user_7")

    assert api.calls[1]["payload"] == {
        "email": "empresa@exemplo.com",
        "first_name": "Empresa",
        "last_name": "LTDA",
        "identification": {"type": "CNPJ", "number": "11222333000181"},
        "description": "user_7",
    }
    assert response.cus_id == "5678-def"
    assert response.external_reference == "user_7"
