from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse
from datetime import timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.infra.config import settings


class CreateSubscriptionRequest(BaseModel):
    model_config = ConfigDict(
        use_enum_values=False,
        json_schema_extra={
            "example": {
                "customer_provider_id": "cus_123",
                "value": "129.90",
                "subscription_type": "MONTHLY",
                "next_due_date": "2026-05-01",
                "description": "Plano Pro anualizado",
                "system": "NEECTIFY_SHOP",
                "system_sub_id": "sub_shop_001",
                "expires_at": "2027-05-01T00:00:00Z",
                "webhook_link": "https://hooks.neectify.local/billing/subscription",
            }
        },
    )

    customer_provider_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identificador do cliente no gateway/provedor de pagamento.",
        examples=["cus_123"],
    )
    value: Decimal = Field(
        ...,
        gt=0,
        description="Valor da assinatura em moeda decimal. Exemplo: `129.90`.",
        examples=["129.90"],
    )
    subscription_type: SubscriptionType = Field(
        ...,
        description="Periodicidade da cobrança recorrente.",
        examples=["MONTHLY"],
    )
    next_due_date: date | None = Field(
        default=None,
        description="Próxima data de cobrança. Se enviada, não pode estar no passado.",
        examples=["2026-05-01"],
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Descrição funcional da assinatura, usada para rastreabilidade operacional.",
        examples=["Plano Pro anualizado"],
    )
    system: System = Field(
        ...,
        description="Sistema interno de origem da requisição. Deve bater com o header `X-System`.",
        examples=["NEECTIFY_SHOP"],
    )
    system_sub_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identificador da assinatura no sistema de origem.",
        examples=["sub_shop_001"],
    )
    expires_at: datetime = Field(
        ...,
        description="Data e hora limite da assinatura. Deve estar no futuro.",
        examples=["2027-05-01T00:00:00Z"],
    )
    webhook_link: str = Field(
        ...,
        max_length=2048,
        description=(
            "Endpoint HTTPS interno que receberá callbacks normalizados do Billing Core. "
            "O host precisa estar permitido em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`."
        ),
        examples=["https://hooks.neectify.local/billing/subscription"],
    )

    @field_validator("customer_provider_id", "description", "system_sub_id")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Campo obrigatório.")
        return cleaned

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
            raise ValueError("Host do webhook interno não permitido.")

        return value

    @field_validator("next_due_date")
    @classmethod
    def validate_next_due_date(cls, value: date | None) -> date | None:
        if value is None:
            return value

        if value < datetime.now(timezone.utc).date():
            raise ValueError("next_due_date nao pode estar no passado.")

        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("expires_at deve estar no futuro.")
        return normalized

    def to_worker_payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"customer_provider_id"})
