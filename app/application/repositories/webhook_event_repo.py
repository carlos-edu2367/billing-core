from abc import ABC, abstractmethod
from app.domain.entities.webhook_event import WebhookEvent

class WebhookEventRepository(ABC):
    @abstractmethod
    async def save(self, event: WebhookEvent) -> WebhookEvent:
        pass

    @abstractmethod
    async def get_by_event_id(self, id: str) -> WebhookEvent:
        pass
