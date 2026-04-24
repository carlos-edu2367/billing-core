from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System


class CreateSubscriptionDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    value: Decimal
    subscription_type: SubscriptionType
    next_due_date: date | None
    description: str
    system: System
    system_sub_id: str
    expires_at: datetime
    webhook_link: str
