from datetime import datetime, timezone

from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.payment import Payment
from app.domain.errors import DomainError


class SyncCheckoutStatus:
    """Aplica o status remoto de um checkout pendente.

    Existe para gateways que nao notificam a expiracao do checkout (Mercado Pago).
    """

    def __init__(self, get_gateway: GetGateway, uow: UowProvider, payment_repo: PaymentRepository):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo

    async def execute(self, payment: Payment) -> Payment | None:
        gateway = self.get_gateway.get(payment.gateway)
        checkout = await gateway.get_checkout(payment.provider_payment_id)
        if checkout.external_reference != payment.external_reference:
            raise DomainError("Checkout remoto retornou external_reference divergente durante sincronizacao.")

        remote_status = checkout.status.upper()
        if remote_status == "PAID":
            payment.mark_as_paid(payment_date=datetime.now(timezone.utc))
        elif remote_status == "EXPIRED":
            payment.mark_as_expired()
        elif remote_status == "CANCELED":
            payment.mark_as_canceled()
        else:
            return None

        payment = await self.payment_repo.save(payment)
        await self.uow.commit()
        return payment
