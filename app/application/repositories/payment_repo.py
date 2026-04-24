from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.payment import Payment
from app.domain.enums.system import System

class PaymentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, id: UUID) -> Payment:
        pass

    @abstractmethod
    async def get_by_provider_id(self, provider_id: str) -> Payment | None:
        pass
 
    @abstractmethod
    async def get_by_system_id(self, system_id: str) -> Payment | None:
        pass

    @abstractmethod
    async def list_by_system(self, system: System, limit: int, page: int) -> list[Payment]:
        pass

    @abstractmethod
    async def list_by_subscription_id(self, sub_id: UUID) -> list[Payment]:
        """
        Retorna todos os pagamentos de uma assinatura, do mais novo ao mais antigo.
        """
        pass
    
    @abstractmethod
    async def save(self, payment: Payment) -> Payment:
        pass
