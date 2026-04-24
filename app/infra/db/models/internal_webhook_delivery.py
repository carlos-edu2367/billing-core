from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery
from app.domain.enums.internal_webhook_delivery_status import InternalWebhookDeliveryStatus
from app.infra.db.setup import Base
from app.infra.db.types.enum_value_type import EnumValueType


class InternalWebhookDeliveryModel(Base):
    __tablename__ = "internal_webhook_deliveries"
    __table_args__ = (
        Index("ix_internal_webhook_deliveries_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_url: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    subscription_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=False, index=True)
    payment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("payments.id"), nullable=True, index=True)
    status: Mapped[InternalWebhookDeliveryStatus] = mapped_column(EnumValueType(InternalWebhookDeliveryStatus), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_domain(self) -> InternalWebhookDelivery:
        return InternalWebhookDelivery(
            dedupe_key=self.dedupe_key,
            event_type=self.event_type,
            target_url=self.target_url,
            payload=self.payload,
            subscription_id=self.subscription_id,
            payment_id=self.payment_id,
            status=self.status,
            attempt_count=self.attempt_count,
            last_error=self.last_error,
            last_attempt_at=self.last_attempt_at,
            delivered_at=self.delivered_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
            id=self.id,
        )
