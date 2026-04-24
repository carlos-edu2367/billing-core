from abc import ABC, abstractmethod

from app.domain.entities.gateway_operation import GatewayOperation


class GatewayOperationRepository(ABC):
    @abstractmethod
    async def get_by_dedupe_key(self, dedupe_key: str) -> GatewayOperation | None:
        pass

    @abstractmethod
    async def save(self, operation: GatewayOperation) -> GatewayOperation:
        pass
