from enum import Enum
from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict

class InternalEventType(Enum):
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
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
