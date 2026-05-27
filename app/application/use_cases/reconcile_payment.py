from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID

from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.payment import Payment
from app.domain.enums.payment_status import PaymentStatus


def apply_gateway_payment_status(
    payment: Payment,
    status: str,
    payment_date: date | datetime | None,
    net_value: Decimal | None,
) -> bool:
    before = payment.payment_status
    normalized = (status or "").upper()
    
    dt = None
    if payment_date:
        if isinstance(payment_date, datetime):
            dt = payment_date
        else:
            dt = datetime.combine(payment_date, time.min, tzinfo=timezone.utc)

    if normalized == "CONFIRMED":
        payment.mark_as_confirmed(dt, net_value)
    elif normalized == "RECEIVED":
        payment.mark_as_paid(dt, net_value)
    elif normalized == "OVERDUE":
        payment.mark_as_overdue()
    elif normalized in {"REFUNDED", "CHARGEBACK_REQUESTED"}:
        if payment.payment_status in {PaymentStatus.PAID, PaymentStatus.CONFIRMED}:
            payment.mark_as_refunded()
    elif normalized in {"DELETED"}:
        if payment.payment_status in {PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.CONFIRMED}:
            payment.mark_as_canceled()

    return before != payment.payment_status


class ReconcilePayment:
    def __init__(self, get_gateway: GetGateway, uow: UowProvider, payment_repo: PaymentRepository):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo

    async def execute(self, payment_id: UUID) -> Payment | None:
        payment = await self.payment_repo.get_by_id(payment_id)
        if payment.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.CONFIRMED}:
            return None

        gateway = self.get_gateway.get(payment.gateway)
        remote = await gateway.get_payment(payment.provider_payment_id)
        changed = apply_gateway_payment_status(payment, remote.status, remote.payment_date, remote.net_value)
        if not changed:
            return None

        payment = await self.payment_repo.save(payment)
        await self.uow.commit()
        return payment
