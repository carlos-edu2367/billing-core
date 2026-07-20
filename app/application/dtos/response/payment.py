from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType


class PaymentStatusResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    payment_id: UUID
    system_payment_id: str
    value: Decimal
    checkout_url: str | None
    payment_status: PaymentStatus
    billing_type: PaymentType
    due_date: date | None
    paid_date: datetime | None
    updated_at: datetime
