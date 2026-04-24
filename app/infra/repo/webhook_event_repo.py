from app.application.repositories.webhook_event_repo import WebhookEventRepository
from app.domain.entities.webhook_event import WebhookEvent
from app.infra.db.models.webhook_event import WebhookEventModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.errors import NotFoundError
from uuid import UUID

class WebhookEventRepositoryINFRA(WebhookEventRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, event: WebhookEvent) -> WebhookEvent:
        if not event.id:
            new = WebhookEventModel(
                event_id=event.event_id,
                provider=event.provider,
                event_type=event.event_type,
                payload=event.payload,
                processed=event.processed,
                created_at=event.created_at
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()

        r = await self.session.get(WebhookEventModel, event.id)
        if not r:
            raise NotFoundError("Webhook Event Not Found")

        r.processed = event.processed
        await self.session.flush()
        return r.to_domain()

    async def get_by_event_id(self, id: str) -> WebhookEvent | None:
        stmt = select(WebhookEventModel).where(WebhookEventModel.event_id == id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None