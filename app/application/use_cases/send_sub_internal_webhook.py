from app.application.interfaces.internal_webhook import InternalWebhook
from app.application.dtos.response.webhook import SendInternalWebhookSubscription, InternalEventType
from app.application.repositories.subscription_repo import SubscriptionRepository
from app.application.repositories.payment_repo import PaymentRepository
from uuid import UUID
from app.domain.errors import DomainError


class SendSubInternalWebhook():
    def __init__(self, sub_repo: SubscriptionRepository, internal_webhook: InternalWebhook, payment_repo: PaymentRepository):
        self.sub_repo = sub_repo
        self.internal_webhook = internal_webhook
        self.payment_repo = payment_repo

    async def send_payment_received(self, subscription_id: UUID, payment_id: UUID):
        subscription = await self.sub_repo.get_by_id(subscription_id)
        payment = await self.payment_repo.get_by_id(payment_id)

        if payment.subscription_id != subscription.id:
            raise DomainError("O pagamento não é relacionado a essa subscription.")
        if not payment.paid_date:
            raise DomainError("Ainda não foi pago.")
        
        request = SendInternalWebhookSubscription(
            event=InternalEventType.PAYMENT_RECEIVED,
            subscription_id=subscription.id,
            subscription_expires_at=subscription.expires_at.date(),
            payment_date=payment.paid_date.date()
        )

        await self.internal_webhook.send(url=subscription.webhook_link, payload=request)
        return
    
    async def send_payment_refunded(self, subscription_id: UUID, payment_id: UUID):
        subscription = await self.sub_repo.get_by_id(subscription_id)
        payment = await self.payment_repo.get_by_id(payment_id)

        if payment.subscription_id != subscription.id:
            raise DomainError("O pagamento não é relacionado a essa subscription.")
        if not payment.paid_date:
            raise DomainError("Ainda não foi pago.")
        if not payment.refunded_date:
            raise DomainError("Pagamento não foi reembolsado.")
        
        request = SendInternalWebhookSubscription(
            event=InternalEventType.PAYMENT_REFUNDED,
            subscription_id=subscription.id,
            subscription_expires_at=subscription.expires_at.date(),
            payment_date=payment.paid_date.date()
        )

        await self.internal_webhook.send(url=subscription.webhook_link, payload=request)
        return
    
    async def send_subscription_canceled(self, subscription_id: UUID):
        subscription = await self.sub_repo.get_by_id(subscription_id)
        if not subscription.cancelled_at:
            raise DomainError("A assinatura não foi cancelada.")
        
        request = SendInternalWebhookSubscription(
            event=InternalEventType.SUBSCRIPTION_INACTIVATED,
            subscription_id=subscription.id,
            subscription_expires_at=subscription.expires_at.date(),
            payment_date=None
        )
        await self.internal_webhook.send(url=subscription.webhook_link, payload=request)
        return
    