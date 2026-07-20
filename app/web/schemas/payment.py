from decimal import Decimal
from urllib.parse import urlparse

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.application.dtos.request.checkout import CheckoutItemDTO, CreateCheckoutDTO
from app.infra.config import settings


class CreatePaymentRequest(CreateCheckoutDTO):
    model_config = ConfigDict(
        use_enum_values=False,
        extra="forbid",
        json_schema_extra={
            "example": {
                "system": "neectify_shop",
                "system_payment_id": "order-123",
                "description": "Pacote de créditos",
                "value": "72.00",
                "minutes_to_expire": 30,
                "items": [
                    {
                        "external_reference": "pack-100",
                        "name": "100 créditos",
                        "description": "Créditos fiscais",
                        "quantity": 1,
                        "value": "72.00",
                    }
                ],
                "success_url": "https://app.neectify.local/billing/success",
                "cancel_url": "https://app.neectify.local/billing/cancel",
                "expired_url": "https://app.neectify.local/billing/expired",
                "webhook_link": "https://hooks.neectify.local/billing/payment",
            }
        },
    )

    description: str = Field(..., min_length=1, max_length=255)
    value: Decimal = Field(..., gt=0)
    minutes_to_expire: int = Field(..., ge=10, le=1440)
    items: list[CheckoutItemDTO] = Field(..., min_length=1)
    success_url: str = Field(..., max_length=2048)
    cancel_url: str = Field(..., max_length=2048)
    expired_url: str = Field(..., max_length=2048)
    webhook_link: str = Field(..., max_length=2048)

    @field_validator("description", "system_payment_id")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Campo obrigatorio.")
        return cleaned

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: list[CheckoutItemDTO]) -> list[CheckoutItemDTO]:
        for item in value:
            item.external_reference = item.external_reference.strip()
            item.name = item.name.strip()
            item.description = item.description.strip()

            if not item.external_reference or not item.name:
                raise ValueError("external_reference e name sao obrigatorios.")
            if item.quantity <= 0:
                raise ValueError("quantity deve ser positivo.")
            if item.value <= 0:
                raise ValueError("value deve ser positivo.")

        return value

    @field_validator("success_url", "cancel_url", "expired_url")
    @classmethod
    def validate_checkout_redirect_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Redirect do checkout deve usar HTTPS.")

        allowed_hosts = settings.ALLOWED_CHECKOUT_REDIRECT_HOSTS
        if not allowed_hosts:
            raise ValueError("Nenhum host de redirect permitido foi configurado.")

        hostname = parsed.hostname.lower()
        if not any(hostname == allowed.lower() or hostname.endswith(f".{allowed.lower()}") for allowed in allowed_hosts):
            raise ValueError("Host do redirect do checkout nao permitido.")

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
        if not any(hostname == allowed.lower() or hostname.endswith(f".{allowed.lower()}") for allowed in allowed_hosts):
            raise ValueError("Host do webhook interno nao permitido.")

        return value

    @model_validator(mode="after")
    def validate_checkout(self) -> "CreatePaymentRequest":
        expected_value = sum((item.quantity * item.value for item in self.items), Decimal("0"))
        if self.value != expected_value:
            raise ValueError("value deve ser igual a soma dos itens.")

        external_reference = f"checkout:{self.system.value}:{self.system_payment_id}"
        if len(external_reference) > 200:
            raise ValueError("A referencia externa do checkout nao pode exceder 200 caracteres.")

        return self

    def to_worker_payload(self) -> dict:
        return self.model_dump(mode="json")
