from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.subscription_status import SubscriptionStatus


class CancelSubscriptionResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    subscription_id: UUID
    subscription_status: SubscriptionStatus
    cancelled_at: datetime | None
