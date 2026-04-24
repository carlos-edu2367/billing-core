from datetime import datetime, timezone
from uuid import UUID

from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider


class WebhookEvent():
    def __init__(
        self,
        event_id: str,
        provider: GatewayProvider,
        event_type: str,
        payload: dict,
        processed: bool = False,
        created_at: datetime = None,
        id: UUID = None,
    ):
        normalized_event_id = (event_id or "").strip()
        normalized_event_type = (event_type or "").strip()

        if not normalized_event_id:
            raise DomainError("WebhookEvent precisa ter event_id.")

        if not normalized_event_type:
            raise DomainError("WebhookEvent precisa ter event_type.")

        self.id = id
        self.event_id = normalized_event_id
        self.provider = provider
        self.event_type = normalized_event_type
        self.payload = payload
        self.processed = processed
        self.created_at = created_at or datetime.now(timezone.utc)

    def mark_as_processed(self):
        if self.processed:
            return

        self.processed = True
