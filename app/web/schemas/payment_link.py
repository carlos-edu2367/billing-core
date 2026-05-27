from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System
from app.infra.config import settings


class CreatePaymentLinkRequest(BaseModel):
    model_config = ConfigDict(
        use_enum_values=False,
        json_schema_extra={
            "example": {
                "value": "72.00",
                "billing_type": "UNDEFINED",
                "description": "Creditos NF-e - pack_100",
                "due_date_limit_days": 3,
                "system": "marketfy",
                "system_payment_id": "550e8400-e29b-41d4-a716-446655440000",
                "webhook_link": "https://api-marketfy.neectify.com/api/v1/webhooks/billing-core",
            }
        },
    )

    value: Decimal = Field(..., gt=0)
    billing_type: PaymentType = Field(default=PaymentType.UNDEFINED)
    description: str = Field(..., min_length=1, max_length=255)
    due_date_limit_days: int = Field(default=3, ge=1, le=365)
    system: System
    system_payment_id: str = Field(..., min_length=1, max_length=128)
    webhook_link: str = Field(..., max_length=2048)

    @field_validator("description", "system_payment_id")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Campo obrigatorio.")
        return cleaned

    @field_validator("billing_type")
    @classmethod
    def validate_allowed_billing_type(cls, value: PaymentType) -> PaymentType:
        if value == PaymentType.DEBIT_CARD:
            raise ValueError("DEBIT_CARD nao e suportado para link de pagamento Asaas.")
        return value

    @field_validator("webhook_link")
    @classmethod
    def validate_webhook_link(cls, value: str) -> str:
        parsed = urlparse(value)

        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Webhook interno deve usar HTTPS.")

        allowed_hosts = settings.ALLOWED_INTERNAL_WEBHOOK_HOSTS
        if not allowed_hosts:
            raise ValueError("Nenhum host interno permitido foi configurado.")

        hostname = parsed.hostname.lower()
        if not any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in allowed_hosts):
            raise ValueError("Host do webhook interno nao permitido.")

        return value

    def to_worker_payload(self) -> dict:
        return self.model_dump(mode="json")
