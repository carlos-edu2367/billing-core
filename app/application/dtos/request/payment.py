from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class CreatePaymentDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    customer_provider_id: str
    value: Decimal
    billing_type: PaymentType
    due_date: date
    description: str
    system: System
    system_payment_id: str
    webhook_link: str
