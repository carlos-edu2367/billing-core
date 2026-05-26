from enum import Enum
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_status import PaymentStatus

class InternalEventType(Enum):
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    PAYMENT_STATUS_UPDATED = "PAYMENT_STATUS_UPDATED"
    SUBSCRIPTION_INACTIVATED = "SUBSCRIPTION_INACTIVATED"

class ProcessWebhookResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    event: InternalEventType
    payment_id: UUID | None
    subscription_id: UUID | None
    
class SendInternalWebhookSubscription(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    event: InternalEventType
    subscription_id: UUID
    subscription_expires_at: date
    payment_date: date | None


class SendInternalWebhookPayment(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    event: InternalEventType
    payment_id: UUID
    system_payment_id: str
    payment_status: PaymentStatus
    value: Decimal
    paid_date: date | None = None
    checkout_url: str | None = None
