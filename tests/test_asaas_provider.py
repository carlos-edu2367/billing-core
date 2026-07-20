import pytest

from app.domain.errors import DomainError
from app.infra.interfaces.asaas_provider import AsaasProvider


class FakeAsaasAPI:
    def __init__(self, response: dict):
        self.response = response
        self.endpoint = None
        self.payload = None

    async def post(self, endpoint: str, payload: dict):
        self.endpoint = endpoint
        self.payload = payload
        return self.response

    async def get(self, endpoint: str):
        self.endpoint = endpoint
        return self.response


@pytest.mark.asyncio
async def test_asaas_provider_creates_detached_checkout_payload():
    provider = AsaasProvider()
    fake_api = FakeAsaasAPI(
        {
            "id": "checkout_123",
            "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
            "status": "ACTIVE",
            "externalReference": "checkout:marketfy:order-123",
        }
    )
    provider.asaas = fake_api

    response = await provider.create_checkout(
        billing_types=["PIX", "CREDIT_CARD"],
        charge_types=["DETACHED"],
        minutes_to_expire=30,
        external_reference="checkout:marketfy:order-123",
        callback={
            "successUrl": "https://app.test/s",
            "cancelUrl": "https://app.test/c",
            "expiredUrl": "https://app.test/e",
        },
        items=[
            {
                "externalReference": "pack-100",
                "name": "100 créditos",
                "description": "",
                "quantity": 1,
                "value": 72.0,
            }
        ],
    )

    assert fake_api.endpoint == "/checkouts"
    assert fake_api.payload == {
        "billingTypes": ["PIX", "CREDIT_CARD"],
        "chargeTypes": ["DETACHED"],
        "minutesToExpire": 30,
        "externalReference": "checkout:marketfy:order-123",
        "callback": {
            "successUrl": "https://app.test/s",
            "cancelUrl": "https://app.test/c",
            "expiredUrl": "https://app.test/e",
        },
        "items": [
            {
                "externalReference": "pack-100",
                "name": "100 créditos",
                "description": "",
                "quantity": 1,
                "value": 72.0,
            }
        ],
    }
    assert response.checkout_id == "checkout_123"
    assert response.checkout_url == "https://sandbox.asaas.com/checkoutSession/show/checkout_123"
    assert response.status == "ACTIVE"
    assert response.external_reference == "checkout:marketfy:order-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "external_reference"),
    [
        (
            {
                "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
                "status": "ACTIVE",
                "externalReference": "checkout:marketfy:order-123",
            },
            "checkout:marketfy:order-123",
        ),
        (
            {
                "id": "checkout_123",
                "status": "ACTIVE",
                "externalReference": "checkout:marketfy:order-123",
            },
            "checkout:marketfy:order-123",
        ),
        (
            {
                "id": "checkout_123",
                "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
                "externalReference": "checkout:marketfy:order-123",
            },
            "checkout:marketfy:order-123",
        ),
        (
            {
                "id": "checkout_123",
                "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
                "status": "ACTIVE",
            },
            "checkout:marketfy:order-123",
        ),
        (
            {
                "id": "checkout_123",
                "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
                "status": "ACTIVE",
                "externalReference": "checkout:marketfy:another-order",
            },
            "checkout:marketfy:order-123",
        ),
    ],
)
async def test_asaas_provider_rejects_incomplete_or_mismatched_checkout_response(
    response: dict, external_reference: str
):
    provider = AsaasProvider()
    provider.asaas = FakeAsaasAPI(response)

    with pytest.raises(DomainError):
        await provider.create_checkout(
            billing_types=["PIX"],
            charge_types=["DETACHED"],
            minutes_to_expire=30,
            external_reference=external_reference,
            callback={},
            items=[],
        )


@pytest.mark.asyncio
async def test_asaas_provider_gets_checkout_with_same_response_validation():
    provider = AsaasProvider()
    fake_api = FakeAsaasAPI(
        {
            "id": "checkout_123",
            "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
            "status": "PAID",
            "externalReference": "checkout:marketfy:order-123",
        }
    )
    provider.asaas = fake_api

    response = await provider.get_checkout("checkout_123")

    assert fake_api.endpoint == "/checkouts/checkout_123"
    assert response.checkout_id == "checkout_123"
    assert response.checkout_url == "https://sandbox.asaas.com/checkoutSession/show/checkout_123"
    assert response.status == "PAID"
    assert response.external_reference == "checkout:marketfy:order-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"link": "https://checkout", "status": "ACTIVE", "externalReference": "checkout:marketfy:order-123"},
        {"id": "checkout_123", "status": "ACTIVE", "externalReference": "checkout:marketfy:order-123"},
        {"id": "checkout_123", "link": "https://checkout", "externalReference": "checkout:marketfy:order-123"},
        {"id": "checkout_123", "link": "https://checkout", "status": "ACTIVE"},
    ],
)
async def test_asaas_provider_rejects_incomplete_get_checkout_response(response: dict):
    provider = AsaasProvider()
    provider.asaas = FakeAsaasAPI(response)

    with pytest.raises(DomainError, match="incompleta"):
        await provider.get_checkout("checkout_123")


@pytest.mark.asyncio
async def test_asaas_provider_rejects_get_checkout_response_for_another_checkout():
    provider = AsaasProvider()
    provider.asaas = FakeAsaasAPI(
        {
            "id": "checkout_other",
            "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_other",
            "status": "ACTIVE",
            "externalReference": "checkout:marketfy:order-123",
        }
    )

    with pytest.raises(DomainError, match="id divergente"):
        await provider.get_checkout("checkout_123")
