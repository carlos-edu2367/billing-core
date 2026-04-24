from app.infra.db.setup import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from uuid import UUID, uuid4
from app.infra.db.types.enum_value_type import EnumValueType
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.entities.webhook_event import WebhookEvent
from datetime import datetime
from typing import Any



class WebhookEventModel(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_provider_processed_created", "provider", "processed", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    provider: Mapped[GatewayProvider] = mapped_column(EnumValueType(GatewayProvider), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed: Mapped[bool] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, index=True)

    def to_domain(self) -> WebhookEvent:
        return WebhookEvent(
            event_id=self.event_id,
            provider=self.provider,
            event_type=self.event_type,
            payload=self.payload,
            processed=self.processed,
            created_at=self.created_at,
            id=self.id
        )
