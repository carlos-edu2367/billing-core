from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System
from app.infra.config import settings


class CreatePaymentRequest(BaseModel):
    model_config = ConfigDict(
        use_enum_values=False,
        json_schema_extra={
            "example": {
                "customer_provider_id": "cus_123",
                "value": "79.90",
                "billing_type": "UNDEFINED",
                "due_date": "2026-06-10",
                "description": "Pedido 123",
                "system": "neectify_shop",
                "system_payment_id": "order-123",
                "webhook_link": "https://hooks.neectify.local/billing/payment",
            }
        },
    )

    customer_provider_id: str = Field(..., min_length=1, max_length=128)
    value: Decimal = Field(..., gt=0)
    billing_type: PaymentType = Field(default=PaymentType.UNDEFINED)
    due_date: date
    description: str = Field(..., min_length=1, max_length=255)
    system: System
    system_payment_id: str = Field(..., min_length=1, max_length=128)
    webhook_link: str = Field(..., max_length=2048)

    @field_validator("customer_provider_id", "description", "system_payment_id")
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
            raise ValueError("DEBIT_CARD nao e suportado para cobranca avulsa Asaas.")
        return value

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, value: date) -> date:
        if value < datetime.now(timezone.utc).date():
            raise ValueError("due_date nao pode estar no passado.")
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
        return self.model_dump(mode="json", exclude={"customer_provider_id"})
