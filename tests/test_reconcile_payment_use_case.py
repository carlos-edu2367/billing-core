from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.interfaces.gateway_provider import PaymentStatusGatewayResponse
from app.application.use_cases.reconcile_payment import ReconcilePayment, apply_gateway_payment_status
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class FakeGateway:
    def __init__(self, status: str = "RECEIVED"):
        self.status = status
        self.get_payment_called = 0

    async def get_payment(self, payment_id: str):
        self.get_payment_called += 1
        return PaymentStatusGatewayResponse(
            payment_id=payment_id,
            status=self.status,
            value=Decimal("79.90"),
            net_value=Decimal("77.90"),
            due_date=date(2026, 6, 10),
            payment_date=date(2026, 6, 11),
            invoice_url="https://www.asaas.com/i/pay_123",
            billing_type="UNDEFINED",
            external_reference="payment:neectify_shop:order-123",
        )


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakePaymentRepo:
    def __init__(self, payment):
        self.payment = payment
        self.saved: list[Payment] = []

    async def get_by_id(self, payment_id):
        return self.payment

    async def save(self, payment: Payment):
        self.saved.append(payment)
        self.payment = payment
        return payment


class FakeUow:
    def __init__(self):
        self.commit_called = 0

    async def commit(self):
        self.commit_called += 1


def make_payment(status=PaymentStatus.PENDING):
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=date(2026, 6, 10),
        external_reference="payment:neectify_shop:order-123",
    )
    payment.id = uuid4()
    payment.payment_status = status
    payment.payment_type = PaymentType.UNDEFINED
    return payment


def test_apply_gateway_payment_status_marks_confirmed():
    payment = make_payment()

    changed = apply_gateway_payment_status(payment, "CONFIRMED", date(2026, 6, 10), Decimal("77.90"))

    assert changed is True
    assert payment.payment_status == PaymentStatus.CONFIRMED
    assert payment.net_value == Decimal("77.90")


@pytest.mark.asyncio
async def test_reconcile_payment_fetches_pending_payment_once_and_persists_change():
    payment = make_payment()
    gateway = FakeGateway(status="RECEIVED")
    uow = FakeUow()
    repo = FakePaymentRepo(payment)
    service = ReconcilePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=uow,
        payment_repo=repo,
    )

    result = await service.execute(payment.id)

    assert result == payment
    assert gateway.get_payment_called == 1
    assert repo.saved[0].payment_status == PaymentStatus.PAID
    assert uow.commit_called == 1


@pytest.mark.asyncio
async def test_reconcile_payment_skips_final_status_without_gateway_call():
    payment = make_payment(status=PaymentStatus.PAID)
    gateway = FakeGateway(status="RECEIVED")
    service = ReconcilePayment(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(payment),
    )

    result = await service.execute(payment.id)

    assert result is None
    assert gateway.get_payment_called == 0
