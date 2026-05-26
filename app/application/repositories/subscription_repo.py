from abc import ABC, abstractmethod
from app.domain.entities.subscription import Subscription
from app.domain.enums.system import System
from uuid import UUID

class SubscriptionRepository(ABC):
    @abstractmethod
    async def get_by_id(self, id: UUID) -> Subscription:
        pass

    @abstractmethod
    async def get_by_id_for_update(self, id: UUID) -> Subscription:
        pass

    @abstractmethod
    async def get_by_provider_id(self, provider_id: str) -> Subscription:
        pass

    @abstractmethod
    async def get_by_system_id(self, system_id: str) -> Subscription:
        pass

    @abstractmethod
    async def get_by_system_ref(self, system_id: str, system: System) -> Subscription | None:
        pass

    @abstractmethod
    async def list_by_system(self, system: System, limit: int, page: int) -> list[Subscription]:
        pass

    @abstractmethod
    async def save(self, sub: Subscription) -> Subscription:
        pass

