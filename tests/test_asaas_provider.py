from decimal import Decimal

import pytest

from app.domain.enums.payment_type import PaymentType
from app.infra.interfaces.asaas_provider import AsaasProvider


class FakeAsaasAPI:
    def __init__(self):
        self.endpoint = None
        self.payload = None

    async def post(self, endpoint: str, payload: dict):
        self.endpoint = endpoint
        self.payload = payload
        return {
            "id": "pml_123",
            "url": "https://www.asaas.com/c/pml_123",
        }


@pytest.mark.asyncio
async def test_asaas_provider_creates_detached_payment_link_payload():
    provider = AsaasProvider()
    fake_api = FakeAsaasAPI()
    provider.asaas = fake_api

    response = await provider.create_payment_link(
        name="Creditos NF-e - pack_100",
        value=Decimal("72.00"),
        billing_type=PaymentType.UNDEFINED,
        description="Creditos NF-e - pack_100",
        external_reference="payment:marketfy:pack-100",
        due_date_limit_days=3,
    )

    assert fake_api.endpoint == "/paymentLinks"
    assert fake_api.payload == {
        "name": "Creditos NF-e - pack_100",
        "value": 72.0,
        "billingType": "UNDEFINED",
        "chargeType": "DETACHED",
        "dueDateLimitDays": 3,
        "description": "Creditos NF-e - pack_100",
        "externalReference": "payment:marketfy:pack-100",
    }
    assert response.payment_link_id == "pml_123"
    assert response.checkout_url == "https://www.asaas.com/c/pml_123"
