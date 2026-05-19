from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.enums.gateway_provider import GatewayProvider


class EventType(str, Enum):
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_OVERDUE = "PAYMENT_OVERDUE"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    PAYMENT_CHARGEBACK_REQUESTED = "PAYMENT_CHARGEBACK_REQUESTED"
    SUBSCRIPTION_INACTIVATED = "SUBSCRIPTION_INACTIVATED"
    SUBSCRIPTION_DELETED = "SUBSCRIPTION_DELETED"
    UNKNOWN = "__UNKNOWN__"


class Details(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None
    subscription: str | None
    status: str | None
    value: Decimal | None
    net_value: Decimal | None
    payment_date: datetime | None
    external_reference: str | None

    @field_validator("payment_date", mode="before")
    @classmethod
    def normalize_payment_date(cls, value):
        if value is None or isinstance(value, datetime):
            return value

        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc)

        parsed = date.fromisoformat(value)
        return datetime.combine(parsed, time.min, tzinfo=timezone.utc)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: EventType
    source_event_id: str | None = None
    details: Details

    @field_validator("event", mode="before")
    @classmethod
    def coerce_unknown_event(cls, v):
        if isinstance(v, str):
            try:
                return EventType(v)
            except ValueError:
                return EventType.UNKNOWN
        return v

    def event_id_for(self, gateway_provider: GatewayProvider) -> str:
        event_source_id = self.source_event_id or self.details.id
        if not event_source_id:
            raise ValueError("Webhook sem identificador tecnico suficiente para idempotencia.")

        subscription_id = self.details.subscription or "no-subscription"
        return f"{gateway_provider.value}:{self.event.value}:{event_source_id}:{subscription_id}"
