from app.domain.enums.system import System
from app.domain.entities.customer import Customer
from app.infra.db.models.customer import BillingCustomerModel
from app.application.repositories.customer_repo import CustomerRepository
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.errors import NotFoundError

class CustomerRepositoryINFRA(CustomerRepository):
    def __init__(self, session:AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> Customer:
        r = await self.session.get(BillingCustomerModel, id)
        if not r:
            raise NotFoundError("Customer Not Found")
        return r.to_domain()

    async def get_by_provider_id(self, provider_id: str) -> Customer:
        stmt = select(BillingCustomerModel).where(BillingCustomerModel.provider_customer_id == provider_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        if not r:
            raise NotFoundError("Customer Not Found")
        return r.to_domain()

    async def get_by_system_id(self, system_id: str) -> Customer:
        stmt = select(BillingCustomerModel).where(BillingCustomerModel.system_customer_id == system_id)
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        if not r:
            raise NotFoundError("Customer Not Found")
        return r.to_domain()

    async def get_by_system_id_and_system(self, system_id: str, system: System) -> Customer:
        stmt = select(BillingCustomerModel).where(
            BillingCustomerModel.system_customer_id == system_id,
            BillingCustomerModel.system == system
        )
        r = await self.session.execute(stmt)
        r = r.scalars().one_or_none()
        if not r:
            raise NotFoundError("Customer Not Found")
        return r.to_domain()

    async def save(self, customer: Customer) -> Customer:
        if not customer.id:
            new = BillingCustomerModel(
                nome=customer.nome,
                email=customer.email.value,
                cpf=customer.cpf.value if customer.cpf else None,
                cnpj=customer.cnpj.value if customer.cnpj else None,
                provider_customer_id=customer.provider_customer_id,
                system_customer_id=customer.system_customer_id,
                gateway_provider=customer.gateway_provider,
                system=customer.system
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()
        
        r = await self.session.get(BillingCustomerModel, customer.id)
        if not r:
            raise NotFoundError("Customer Not Found")
        
        r.nome = customer.nome
        r.email = customer.email.value
        r.cpf = customer.cpf.value if customer.cpf else None
        r.cnpj = customer.cnpj.value if customer.cnpj else None
        r.provider_customer_id = customer.provider_customer_id
        r.system_customer_id = customer.system_customer_id
        r.gateway_provider = customer.gateway_provider
        r.system = customer.system
        await self.session.flush()
        return r.to_domain()