from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CancelSubscriptionRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "Cancelado a pedido do cliente apos downgrade.",
            }
        }
    )

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Motivo opcional do cancelamento para rastreabilidade operacional.",
        examples=["Cancelado a pedido do cliente apos downgrade."],
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    def to_worker_payload(self, subscription_id: UUID, system: str, job_id: str) -> dict:
        payload = {
            "subscription_id": str(subscription_id),
            "system": system,
            "reason": self.reason,
        }
        if job_id:
            payload["job_id"] = job_id
        return payload
