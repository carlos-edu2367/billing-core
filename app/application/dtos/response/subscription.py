from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.subscription_status import SubscriptionStatus


class CreateSubscriptionResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    subscription_id: UUID
    payment_id: UUID
    value: Decimal
    checkout_url: str | None
    payment_status: PaymentStatus
    subscription_status: SubscriptionStatus
