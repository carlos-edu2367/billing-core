from app.infra.db.setup import Base
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infra.db.types.enum_value_type import EnumValueType
from app.domain.entities.customer import Customer
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.domain.value_objects.cnpj import CNPJ
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email



class BillingCustomerModel(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("provider_customer_id", name="uq_customers_provider_ref"),
        UniqueConstraint("system_customer_id", "system", name="uq_customers_system_ref"),
        CheckConstraint("(cpf IS NOT NULL) <> (cnpj IS NOT NULL)", name="ck_customers_single_document"),
        Index("ix_customers_system_gateway", "system", "gateway_provider"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    cpf: Mapped[str] = mapped_column(String(11), nullable=True)
    cnpj: Mapped[str] = mapped_column(String(14), nullable=True)
    provider_customer_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    system_customer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    gateway_provider: Mapped[GatewayProvider] = mapped_column(EnumValueType(GatewayProvider), nullable=False, default=GatewayProvider.ASAAS)
    system: Mapped[System] = mapped_column(EnumValueType(System), nullable=False, default=System.NEECTIFY_SHOP, index=True)

    def to_domain(self) -> Customer:
        return Customer(
            nome=self.nome,
            email=Email(self.email),
            cpf=CPF(self.cpf) if self.cpf else None,
            cnpj=CNPJ(self.cnpj) if self.cnpj else None,
            id=self.id,
            provider_customer_id=self.provider_customer_id,
            system_customer_id=self.system_customer_id,
            gateway_provider=self.gateway_provider,
            system=self.system
        )
