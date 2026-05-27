from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class CreatePaymentLinkDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    value: Decimal
    billing_type: PaymentType
    description: str
    due_date_limit_days: int = 3
    system: System
    system_payment_id: str
    webhook_link: str
