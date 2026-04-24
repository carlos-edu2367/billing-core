from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.repositories.internal_webhook_delivery_repo import InternalWebhookDeliveryRepository
from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery
from app.domain.errors import NotFoundError
from app.infra.db.models.internal_webhook_delivery import InternalWebhookDeliveryModel


class InternalWebhookDeliveryRepositoryINFRA(InternalWebhookDeliveryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> InternalWebhookDelivery:
        record = await self.session.get(InternalWebhookDeliveryModel, id)
        if not record:
            raise NotFoundError("Internal Webhook Delivery Not Found")
        return record.to_domain()

    async def get_by_dedupe_key(self, dedupe_key: str) -> InternalWebhookDelivery | None:
        stmt = select(InternalWebhookDeliveryModel).where(InternalWebhookDeliveryModel.dedupe_key == dedupe_key)
        result = await self.session.execute(stmt)
        record = result.scalars().one_or_none()
        return record.to_domain() if record else None

    async def save(self, delivery: InternalWebhookDelivery) -> InternalWebhookDelivery:
        if not delivery.id:
            new = InternalWebhookDeliveryModel(
                dedupe_key=delivery.dedupe_key,
                event_type=delivery.event_type,
                target_url=delivery.target_url,
                payload=delivery.payload,
                subscription_id=delivery.subscription_id,
                payment_id=delivery.payment_id,
                status=delivery.status,
                attempt_count=delivery.attempt_count,
                last_error=delivery.last_error,
                last_attempt_at=delivery.last_attempt_at,
                delivered_at=delivery.delivered_at,
                created_at=delivery.created_at,
                updated_at=delivery.updated_at,
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()

        record = await self.session.get(InternalWebhookDeliveryModel, delivery.id)
        if not record:
            raise NotFoundError("Internal Webhook Delivery Not Found")

        record.status = delivery.status
        record.attempt_count = delivery.attempt_count
        record.last_error = delivery.last_error
        record.last_attempt_at = delivery.last_attempt_at
        record.delivered_at = delivery.delivered_at
        record.updated_at = delivery.updated_at
        await self.session.flush()
        return record.to_domain()
