from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.errors import DomainError
from app.infra.config import settings
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
PREAPPROVAL_ID = "2c938084726fca480172750000000000"
INIT_POINT = f"https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id={PREAPPROVAL_ID}"


def make_provider(api):
    return MercadoPagoProvider(api=api, now=lambda: NOW)


def preapproval(**overrides):
    base = {
        "id": PREAPPROVAL_ID,
        "status": "pending",
        "init_point": INIT_POINT,
        "next_payment_date": "2026-10-01T12:00:00.000-03:00",
        "auto_recurring": {"frequency": 6, "frequency_type": "months", "transaction_amount": 129.9, "currency_id": "BRL"},
    }
    return base | overrides


async def test_create_subscription_posts_pending_preapproval_for_customer_email(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"},
            ("POST", "/preapproval"): preapproval(),
        }
    )

    subscription_id = await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.SEMIANNUAL,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
    )

    post = api.calls[1]
    assert subscription_id == PREAPPROVAL_ID
    assert post["idempotency_key"] == "preapproval:sub_marketfy_1"
    assert post["payload"] == {
        "reason": "Plano Pro",
        "external_reference": "sub_marketfy_1",
        "payer_email": "joao@exemplo.com",
        "auto_recurring": {
            "frequency": 6,
            "frequency_type": "months",
            "transaction_amount": 129.9,
            "currency_id": "BRL",
            "start_date": "2026-10-01T00:00:00.000+00:00",
        },
        "back_url": settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL,
        "status": "pending",
    }


async def test_create_subscription_uses_request_back_url_when_present(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"},
            ("POST", "/preapproval"): preapproval(),
        }
    )

    await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.SEMIANNUAL,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
        back_url="https://app.marketfy.com/billing/retorno?tipo=subscription&ref=sub_marketfy_1",
    )

    assert api.calls[1]["payload"]["back_url"] == "https://app.marketfy.com/billing/retorno?tipo=subscription&ref=sub_marketfy_1"


async def test_create_subscription_falls_back_to_settings_back_url_when_absent(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"},
            ("POST", "/preapproval"): preapproval(),
        }
    )

    await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.SEMIANNUAL,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
    )

    assert api.calls[1]["payload"]["back_url"] == settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL


async def test_create_subscription_starting_today_omits_start_date(fake_mp_api):
    api = fake_mp_api({("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"}, ("POST", "/preapproval"): preapproval()})

    await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=NOW.date(),
        cycle=SubscriptionType.MONTHLY,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
    )

    assert "start_date" not in api.calls[1]["payload"]["auto_recurring"]
    assert api.calls[1]["payload"]["auto_recurring"]["frequency"] == 1


@pytest.mark.parametrize("billing_type", [PaymentType.PIX, PaymentType.BOLETO, PaymentType.DEBIT_CARD])
async def test_create_subscription_rejects_non_credit_card(fake_mp_api, billing_type):
    with pytest.raises(DomainError):
        await make_provider(fake_mp_api({})).create_subscription(
            customer_provider_id="cus-1",
            billing_type=billing_type,
            value=Decimal("10"),
            next_due_date=NOW.date(),
            cycle=SubscriptionType.MONTHLY,
            description="Plano",
        )


async def test_subscription_without_invoices_returns_provisional_payment_with_init_point(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(),
            ("GET", "/authorized_payments/search"): {"results": []},
        }
    )

    payments = await make_provider(api).get_subscription_payment(PREAPPROVAL_ID)

    assert len(payments) == 1
    assert payments[0].payment_id == PREAPPROVAL_ID
    assert payments[0].status == "PENDING"
    assert payments[0].invoice_url == INIT_POINT
    assert payments[0].value == Decimal("129.9")
    assert payments[0].due_date == date(2026, 10, 1)
    assert payments[0].billing_type == "CREDIT_CARD"
    assert api.calls[1]["params"] == {"preapproval_id": PREAPPROVAL_ID}


async def test_subscription_with_invoices_returns_real_payments(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status="authorized"),
            ("GET", "/authorized_payments/search"): {
                "results": [
                    {"id": 7001, "transaction_amount": 129.9, "debit_date": "2026-09-14T13:00:00.000-03:00", "payment": {"id": 123456, "status": "approved"}},
                    {"id": 7002, "transaction_amount": 129.9, "debit_date": "2027-03-14T13:00:00.000-03:00", "status": "scheduled"},
                ]
            },
        }
    )

    payments = await make_provider(api).get_subscription_payment(PREAPPROVAL_ID)

    assert [payment.payment_id for payment in payments] == ["123456"]
    assert payments[0].status == "RECEIVED"


async def test_cancel_subscription_puts_cancelled_status(fake_mp_api):
    api = fake_mp_api({("PUT", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status="cancelled")})

    reference = await make_provider(api).cancel_subscription(PREAPPROVAL_ID)

    assert reference == PREAPPROVAL_ID
    assert api.calls[0]["payload"] == {"status": "cancelled"}


@pytest.mark.parametrize(
    ("status", "expected", "deleted"),
    [("authorized", "ACTIVE", False), ("pending", "PENDING", False), ("paused", "PAUSED", False), ("cancelled", "CANCELED", True)],
)
async def test_verify_status_translates_preapproval(fake_mp_api, status, expected, deleted):
    api = fake_mp_api({("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status=status)})

    response = await make_provider(api).verify_status(PREAPPROVAL_ID)

    assert response.status == expected
    assert response.deleted is deleted
    assert response.cycle == "SEMIANNUALLY"
    assert response.value == Decimal("129.9")
    assert response.next_due_date == date(2026, 10, 1)


def test_gateway_factory_resolves_mercadopago():
    assert isinstance(GetGatewayInfra().get(GatewayProvider.MERCADOPAGO), MercadoPagoProvider)
