from app.infra.db.setup import Base
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import DECIMAL, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infra.db.types.enum_value_type import EnumValueType
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.movimentation_type import MovimentationType
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System

class PaymentModel(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("system_payment_id", "from_system", name="uq_payments_system_ref"),
        UniqueConstraint("provider_payment_id", name="uq_payments_provider_ref"),
        Index("ix_payments_subscription_paid_date", "subscription_id", "paid_date"),
        Index("ix_payments_system_status_paid_date", "from_system", "payment_status", "paid_date"),
        Index("ix_payments_system_status_created", "from_system", "payment_status", "created_at"),
        Index("ix_payments_system_external_reference", "from_system", "external_reference"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    gateway: Mapped[GatewayProvider] = mapped_column(EnumValueType(GatewayProvider), nullable=False, index=True)
    system_payment_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String, nullable=True)
    net_value: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=True)
    value: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    from_system: Mapped[System] = mapped_column(EnumValueType(System), nullable=False, index=True)
    payment_status: Mapped[PaymentStatus] = mapped_column(EnumValueType(PaymentStatus), nullable=False, index=True)
    payment_type: Mapped[PaymentType] = mapped_column(EnumValueType(PaymentType), nullable=False)
    paid_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    canceled_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    movimentation_type: Mapped[MovimentationType] = mapped_column(EnumValueType(MovimentationType), nullable=False)
    checkout_link: Mapped[str] = mapped_column(String, nullable=True)
    subscription_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True)
    webhook_link: Mapped[str] = mapped_column(String, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=True, index=True)
    external_reference: Mapped[str] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    subscription: Mapped["SubscriptionModel"] = relationship(back_populates="payments", lazy="joined")


    def to_domain(self) -> Payment:
        return Payment(
            description=self.description,
            gateway=self.gateway,
            system_payment_id=self.system_payment_id,
            provider_payment_id=self.provider_payment_id,
            net_value=self.net_value,
            value=self.value,
            from_system=self.from_system,
            payment_status=self.payment_status,
            payment_type=self.payment_type,
            paid_date=self.paid_date,
            canceled_date=self.canceled_date,
            refunded_date=self.refunded_date,
            failed_date=self.failed_date,
            movimentation_type=self.movimentation_type,
            checkout_link=self.checkout_link,
            id=self.id,
            subscription_id=self.subscription_id,
            webhook_link=self.webhook_link,
            due_date=self.due_date,
            external_reference=self.external_reference,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
