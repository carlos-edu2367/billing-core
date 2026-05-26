from abc import ABC, abstractmethod

class InternalWebhook(ABC):
    @abstractmethod
    async def send(self, url: str, payload, webhook_id: str | None = None, event_type: str | None = None):
        pass

    
