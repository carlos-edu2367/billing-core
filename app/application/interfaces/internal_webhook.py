from abc import ABC, abstractmethod

class InternalWebhook(ABC):
    @abstractmethod
    async def send(self, url: str, payload):
        pass

    