from app.infra.db.setup import Base
from uuid import UUID, uuid4
from sqlalchemy import DECIMAL, DateTime, Index, Integer, String, UniqueConstraint
from datetime import datetime
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infra.db.types.enum_value_type import EnumValueType
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from decimal import Decimal

class SubscriptionModel(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("system_subscription_id", "from_system", name="uq_subscriptions_system_ref"),
        UniqueConstraint("gateway_subscription_id", name="uq_subscriptions_gateway_ref"),
        Index("ix_subscriptions_system_status_due", "from_system", "status", "next_due_date"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    initial_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    system_subscription_id: Mapped[str] = mapped_column(String, nullable=False)
    gateway_subscription_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    gateway_provider: Mapped[GatewayProvider] = mapped_column(EnumValueType(GatewayProvider), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(EnumValueType(SubscriptionStatus), nullable=False, index=True)
    last_paid_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    from_system: Mapped[System] = mapped_column(EnumValueType(System), nullable=False, index=True)
    subscription_type: Mapped[SubscriptionType] = mapped_column(EnumValueType(SubscriptionType), nullable=False, index=True)
    value: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_link: Mapped[str] = mapped_column(String, nullable=True)

    payments: Mapped[list["PaymentModel"]] = relationship(back_populates="subscription", cascade="all, delete-orphan")

    def to_domain(self) -> Subscription:
        return Subscription(
            initial_date=self.initial_date,
            description=self.description,
            system_subscription_id=self.system_subscription_id,
            gateway_subscription_id=self.gateway_subscription_id,
            gateway_provider=self.gateway_provider,
            status=self.status,
            last_paid_date=self.last_paid_date,
            from_system=self.from_system,
            subscription_type=self.subscription_type,
            expires_at=self.expires_at,
            value=self.value,
            trial_days=self.trial_days,
            trial_ends_at=self.trial_ends_at,
            next_due_date=self.next_due_date,
            cancelled_at=self.cancelled_at,
            id=self.id,
            webhook_link=self.webhook_link
        )
