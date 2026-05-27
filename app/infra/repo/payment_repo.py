from app.domain.entities.payment import Payment
from app.domain.enums.system import System
from app.infra.db.models.payment import PaymentModel
from app.application.repositories.payment_repo import PaymentRepository
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.errors import NotFoundError


class PaymentRepositoryINFRA(PaymentRepository):
    def __init__(self, session:AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> Payment:
        r = await self.session.get(PaymentModel, id)
        if not r:
            raise NotFoundError("Payment Not Found")
        return r.to_domain()
    
    async def get_by_provider_id(self, provider_id: str) -> Payment | None:
        stmt = select(PaymentModel).where(PaymentModel.provider_payment_id == provider_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None
    
    async def get_by_system_id(self, system_id: str) -> Payment | None:
        stmt = select(PaymentModel).where(PaymentModel.system_payment_id == system_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None

    async def get_by_system_ref(self, system_id: str, system: System) -> Payment | None:
        stmt = select(PaymentModel).where(
            PaymentModel.system_payment_id == system_id,
            PaymentModel.from_system == system,
        )
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None

    async def get_by_external_reference(self, external_reference: str) -> Payment | None:
        stmt = select(PaymentModel).where(PaymentModel.external_reference == external_reference)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        return r.to_domain() if r else None
    
    async def list_by_system(self, system: System, limit: int, page: int) -> list[Payment]:
        stmt = select(PaymentModel).where(PaymentModel.from_system == system).limit(limit).offset((page - 1) * limit)
        r = await self.session.execute(stmt)
        r = r.scalars().all()
        return [x.to_domain() for x in r]
    
    async def list_by_subscription_id(self, sub_id: UUID) -> list[Payment]:
        """
        Retorna todos os pagamentos de uma assinatura, do mais novo ao mais antigo.
        """
        stmt = select(PaymentModel).where(PaymentModel.subscription_id == sub_id).order_by(PaymentModel.paid_date.desc())
        r = await self.session.execute(stmt)
        r = r.scalars().all()
        return [x.to_domain() for x in r]
    
    async def save(self, payment: Payment) -> Payment:
        if not payment.id:
            new = PaymentModel(
                description=payment.description,
                gateway=payment.gateway,
                system_payment_id=payment.system_payment_id,
                provider_payment_id=payment.provider_payment_id,
                net_value=payment.net_value,
                value=payment.value,
                from_system=payment.from_system,
                payment_status=payment.payment_status,
                payment_type=payment.payment_type,
                paid_date=payment.paid_date,
                canceled_date=payment.canceled_date,
                refunded_date=payment.refunded_date,
                failed_date=payment.failed_date,
                movimentation_type=payment.movimentation_type,
                checkout_link=payment.checkout_link,
                subscription_id=payment.subscription_id,
                webhook_link=payment.webhook_link,
                due_date=payment.due_date,
                external_reference=payment.external_reference,
                created_at=payment.created_at,
                updated_at=payment.updated_at,
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()
        
        r = await self.session.get(PaymentModel, payment.id)
        if not r:
            raise NotFoundError("Payment Not Found")
        
        r.description = payment.description
        r.gateway = payment.gateway
        r.system_payment_id = payment.system_payment_id
        r.provider_payment_id = payment.provider_payment_id
        r.net_value = payment.net_value
        r.value = payment.value
        r.from_system = payment.from_system
        r.payment_status = payment.payment_status
        r.payment_type = payment.payment_type
        r.paid_date = payment.paid_date
        r.canceled_date = payment.canceled_date
        r.refunded_date = payment.refunded_date
        r.failed_date = payment.failed_date
        r.movimentation_type = payment.movimentation_type
        r.checkout_link = payment.checkout_link
        r.subscription_id = payment.subscription_id
        r.webhook_link = payment.webhook_link
        r.due_date = payment.due_date
        r.external_reference = payment.external_reference
        r.created_at = payment.created_at
        r.updated_at = payment.updated_at
        await self.session.flush()
        return r.to_domain()
    
