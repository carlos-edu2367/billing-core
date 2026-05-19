import logging

from app.application.dtos.request.webhook import EventType, WebhookPayload
from app.application.dtos.response.webhook import InternalEventType, ProcessWebhookResponse
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.payment_repo import PaymentRepository
from app.application.repositories.subscription_repo import SubscriptionRepository
from app.application.repositories.webhook_event_repo import WebhookEventRepository
from app.domain.entities.payment import Payment
from app.domain.entities.webhook_event import WebhookEvent
from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.subscription_status import SubscriptionStatus

logger = logging.getLogger(__name__)


class ProcessWebhookService():
    def __init__(self, payment_repo: PaymentRepository, sub_repo: SubscriptionRepository, uow: UowProvider, webhook_event_repo: WebhookEventRepository):
        self.payment_repo = payment_repo
        self.sub_repo = sub_repo
        self.uow = uow
        self.webhook_event_repo = webhook_event_repo

    async def execute(self, gateway_provider: GatewayProvider, payload: WebhookPayload) -> ProcessWebhookResponse | None:
        event_id = payload.event_id_for(gateway_provider)
        event = await self.webhook_event_repo.get_by_event_id(event_id)

        if event and event.processed:
            return None

        if event is None:
            event = WebhookEvent(
                event_id=event_id,
                provider=gateway_provider,
                event_type=payload.event.value,
                payload=payload.model_dump(mode="json"),
            )

        if payload.event in (
            EventType.UNKNOWN,
            EventType.PAYMENT_OVERDUE,
            EventType.PAYMENT_CHARGEBACK_REQUESTED,
            EventType.PAYMENT_REFUNDED,
        ):
            logger.warning(
                "Webhook recebido sem ação implementada",
                extra={"event": payload.event.value, "event_id": event_id},
            )
            event.mark_as_processed()
            await self.webhook_event_repo.save(event)
            await self.uow.commit()
            return None

        if payload.event == EventType.PAYMENT_RECEIVED and not payload.details.subscription:
            logger.warning(
                "PAYMENT_RECEIVED sem subscription_id ignorado",
                extra={"event_id": event_id, "payment_id": payload.details.id},
            )
            event.mark_as_processed()
            await self.webhook_event_repo.save(event)
            await self.uow.commit()
            return None

        if payload.event == EventType.PAYMENT_RECEIVED and payload.details.subscription:
            sub = await self.sub_repo.get_by_provider_id(payload.details.subscription)
            payment = await self.payment_repo.get_by_provider_id(payload.details.id)

            if payment and payment.payment_status == PaymentStatus.PAID:
                event.mark_as_processed()
                await self.webhook_event_repo.save(event)
                await self.uow.commit()
                return ProcessWebhookResponse(
                    event=InternalEventType.PAYMENT_RECEIVED,
                    payment_id=payment.id,
                    subscription_id=sub.id,
                )

            if payment is None:
                payment = Payment.create_subscription_payment(
                    description=f"Pagamento relacionado à assinatura: {sub.description}",
                    gateway=sub.gateway_provider,
                    system_payment_id=f"{sub.system_subscription_id}:{payload.details.id}",
                    provider_payment_id=payload.details.id,
                    value=payload.details.value or sub.value,
                    from_system=sub.from_system,
                    subscription_id=sub.id,
                )

            payment_date = payload.details.payment_date or payment.paid_date
            if payment_date is None:
                raise DomainError("Webhook de pagamento recebido sem data de pagamento.")

            payment.mark_as_paid(payment_date=payment_date, net_value=payload.details.net_value)

            if sub.last_paid_date != payment_date:
                sub.mark_as_paid(payment_date=payment_date)

            sub = await self.sub_repo.save(sub)
            payment = await self.payment_repo.save(payment)
            event.mark_as_processed()
            await self.webhook_event_repo.save(event)
            await self.uow.commit()

            return ProcessWebhookResponse(
                event=InternalEventType.PAYMENT_RECEIVED,
                payment_id=payment.id,
                subscription_id=sub.id,
            )

        if payload.event in [EventType.SUBSCRIPTION_DELETED, EventType.SUBSCRIPTION_INACTIVATED]:
            if not payload.details.subscription:
                raise DomainError("Não é possível atualizar uma assinatura sem a identificação.")

            sub = await self.sub_repo.get_by_provider_id(payload.details.subscription)
            if sub.status != SubscriptionStatus.CANCELED:
                sub.cancel()
                await self.sub_repo.save(sub)

            event.mark_as_processed()
            await self.webhook_event_repo.save(event)
            await self.uow.commit()

            return ProcessWebhookResponse(
                event=InternalEventType.SUBSCRIPTION_INACTIVATED,
                payment_id=None,
                subscription_id=sub.id,
            )

        return None
