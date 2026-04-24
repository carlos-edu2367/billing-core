from datetime import datetime, timezone
from uuid import UUID

from app.domain.enums.internal_webhook_delivery_status import InternalWebhookDeliveryStatus
from app.domain.errors import DomainError


class InternalWebhookDelivery:
    def __init__(
        self,
        dedupe_key: str,
        event_type: str,
        target_url: str,
        payload: dict,
        subscription_id: UUID,
        payment_id: UUID | None = None,
        status: InternalWebhookDeliveryStatus = InternalWebhookDeliveryStatus.PENDING,
        attempt_count: int = 0,
        last_error: str | None = None,
        last_attempt_at: datetime | None = None,
        delivered_at: datetime | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        id: UUID | None = None,
    ):
        normalized_dedupe_key = (dedupe_key or "").strip()
        normalized_event_type = (event_type or "").strip()
        normalized_target_url = (target_url or "").strip()

        if not normalized_dedupe_key:
            raise DomainError("InternalWebhookDelivery precisa ter dedupe_key.")
        if not normalized_event_type:
            raise DomainError("InternalWebhookDelivery precisa ter event_type.")
        if not normalized_target_url:
            raise DomainError("InternalWebhookDelivery precisa ter target_url.")

        self.id = id
        self.dedupe_key = normalized_dedupe_key
        self.event_type = normalized_event_type
        self.target_url = normalized_target_url
        self.payload = payload
        self.subscription_id = subscription_id
        self.payment_id = payment_id
        self.status = status
        self.attempt_count = attempt_count
        self.last_error = last_error
        self.last_attempt_at = last_attempt_at
        self.delivered_at = delivered_at
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or self.created_at

    def register_attempt(self) -> None:
        self.attempt_count += 1
        self.last_attempt_at = datetime.now(timezone.utc)
        self.updated_at = self.last_attempt_at

    def mark_retrying(self, error_message: str) -> None:
        self.status = InternalWebhookDeliveryStatus.RETRYING
        self.last_error = (error_message or "").strip() or "Falha transitoria."
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, error_message: str) -> None:
        self.status = InternalWebhookDeliveryStatus.FAILED
        self.last_error = (error_message or "").strip() or "Falha nao detalhada."
        self.updated_at = datetime.now(timezone.utc)

    def mark_delivered(self) -> None:
        self.status = InternalWebhookDeliveryStatus.DELIVERED
        self.last_error = None
        self.delivered_at = datetime.now(timezone.utc)
        self.updated_at = self.delivered_at
