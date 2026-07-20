from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums.system import System


class CheckoutItemDTO(BaseModel):
    external_reference: str
    name: str
    description: str = ""
    quantity: int
    value: Decimal


class CreateCheckoutDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    system: System
    system_payment_id: str
    description: str
    value: Decimal
    minutes_to_expire: int
    items: list[CheckoutItemDTO]
    success_url: str
    cancel_url: str
    expired_url: str
    webhook_link: str
