from app.infra.db.models.subscription import Subscription, SubscriptionModel, System
from app.application.repositories.subscription_repo import SubscriptionRepository
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.errors import NotFoundError

class SubscriptionRepositoryINFRA(SubscriptionRepository):
    def __init__(self, session:AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> Subscription:
        r = await self.session.get(SubscriptionModel, id)
        if not r:
            raise NotFoundError("Subscription Not Found")
        return r.to_domain()

    async def get_by_provider_id(self, provider_id: str) -> Subscription:
        stmt = select(SubscriptionModel).where(SubscriptionModel.gateway_subscription_id == provider_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        if not r:
            raise NotFoundError("Subscription Not Found")
        return r.to_domain()


    async def get_by_system_id(self, system_id: str) -> Subscription:
        stmt = select(SubscriptionModel).where(SubscriptionModel.system_subscription_id == system_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        if not r:
            raise NotFoundError("Subscription Not Found")
        return r.to_domain()

    async def get_by_system_ref(self, system_id: str, system: System) -> Subscription | None:
        stmt = select(SubscriptionModel).where(
            SubscriptionModel.system_subscription_id == system_id,
            SubscriptionModel.from_system == system,
        )
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None


    async def list_by_system(self, system: System, limit: int, page: int) -> list[Subscription]:
        stmt = select(SubscriptionModel).where(SubscriptionModel.from_system == system).limit(limit).offset((page - 1) * limit)
        r = await self.session.execute(stmt)
        r = r.scalars().all()
        return [x.to_domain() for x in r]


    async def save(self, sub: Subscription) -> Subscription:
        if not sub.id:
            new = SubscriptionModel(
                initial_date=sub.initial_date,
                description=sub.description,
                system_subscription_id=sub.system_subscription_id,
                gateway_subscription_id=sub.gateway_subscription_id,
                gateway_provider=sub.gateway_provider,
                status=sub.status,
                last_paid_date=sub.last_paid_date,
                from_system=sub.from_system,
                subscription_type=sub.subscription_type,
                expires_at=sub.expires_at,
                value=sub.value,
                trial_days=sub.trial_days,
                trial_ends_at=sub.trial_ends_at,
                next_due_date=sub.next_due_date,
                cancelled_at=sub.cancelled_at,
                webhook_link=sub.webhook_link
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()
        
        r = await self.session.get(SubscriptionModel, sub.id)
        if not r:
            raise NotFoundError("Subscription Not Found")
        
        r.initial_date = sub.initial_date
        r.description = sub.description
        r.system_subscription_id = sub.system_subscription_id
        r.gateway_subscription_id = sub.gateway_subscription_id
        r.gateway_provider = sub.gateway_provider
        r.status = sub.status
        r.last_paid_date = sub.last_paid_date
        r.from_system = sub.from_system
        r.subscription_type = sub.subscription_type
        r.expires_at = sub.expires_at
        r.value = sub.value
        r.trial_days = sub.trial_days
        r.trial_ends_at = sub.trial_ends_at
        r.next_due_date = sub.next_due_date
        r.cancelled_at = sub.cancelled_at
        r.webhook_link = sub.webhook_link
        
        await self.session.flush()
        return r.to_domain()
