from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.customer import Customer
from app.domain.enums.system import System

class CustomerRepository(ABC):
    @abstractmethod
    async def get_by_id(self, id: UUID) -> Customer:
        pass

    @abstractmethod
    async def get_by_provider_id(self, provider_id: str) -> Customer:
        pass

    @abstractmethod
    async def get_by_system_id(self, system_id: str) -> Customer:
        pass

    @abstractmethod
    async def get_by_system_id_and_system(self, system_id: str, system: System) -> Customer:
        pass

    @abstractmethod
    async def save(self, customer: Customer) -> Customer:
        pass
