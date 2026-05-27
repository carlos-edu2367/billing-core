from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.payment_status import PaymentStatus


class CreatePaymentLinkResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    payment_id: UUID
    checkout_url: str | None
    payment_status: PaymentStatus
