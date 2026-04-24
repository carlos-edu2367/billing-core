from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery


class InternalWebhookDeliveryRepository(ABC):
    @abstractmethod
    async def get_by_id(self, id: UUID) -> InternalWebhookDelivery:
        pass

    @abstractmethod
    async def get_by_dedupe_key(self, dedupe_key: str) -> InternalWebhookDelivery | None:
        pass

    @abstractmethod
    async def save(self, delivery: InternalWebhookDelivery) -> InternalWebhookDelivery:
        pass
