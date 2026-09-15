from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.interfaces.gateway_provider import CreateCheckoutGatewayResponse
from app.application.use_cases.sync_checkout_status import SyncCheckoutStatus
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.system import System
from app.domain.errors import DomainError

REFERENCE = "checkout:marketfy:order-123"


class FakeGateway:
    def __init__(self, status: str, external_reference: str = REFERENCE):
        self.status = status
        self.external_reference = external_reference

    async def get_checkout(self, checkout_id):
        return CreateCheckoutGatewayResponse(
            checkout_id=checkout_id, checkout_url="https://mp/pref-1", status=self.status, external_reference=self.external_reference
        )


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakePaymentRepo:
    def __init__(self):
        self.saved = []

    async def save(self, payment):
        self.saved.append(payment)
        return payment


class FakeUow:
    def __init__(self):
        self.commit_called = 0

    async def commit(self):
        self.commit_called += 1


def make_payment() -> Payment:
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="order-123",
        provider_payment_id="pref-1",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://mp/pref-1",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference=REFERENCE,
    )
    payment.id = uuid4()
    return payment


def make_service(gateway, repo=None, uow=None):
    return SyncCheckoutStatus(get_gateway=FakeGetGateway(gateway), uow=uow or FakeUow(), payment_repo=repo or FakePaymentRepo())


@pytest.mark.parametrize(
    ("remote_status", "local_status"),
    [("PAID", PaymentStatus.PAID), ("EXPIRED", PaymentStatus.EXPIRED)],
)
async def test_final_remote_status_is_applied_and_committed(remote_status, local_status):
    repo, uow = FakePaymentRepo(), FakeUow()
    payment = make_payment()

    result = await make_service(FakeGateway(remote_status), repo, uow).execute(payment)

    assert result.payment_status == local_status
    assert repo.saved == [payment]
    assert uow.commit_called == 1


async def test_active_checkout_is_left_untouched():
    repo = FakePaymentRepo()

    assert await make_service(FakeGateway("ACTIVE"), repo).execute(make_payment()) is None
    assert repo.saved == []


async def test_divergent_reference_is_rejected():
    with pytest.raises(DomainError):
        await make_service(FakeGateway("EXPIRED", external_reference="checkout:marketfy:other")).execute(make_payment())
