from abc import ABC, abstractmethod

from app.domain.entities.gateway_operation import GatewayOperation


from app.domain.enums.gateway_operation_status import GatewayOperationStatus


class GatewayOperationRepository(ABC):
    @abstractmethod
    async def get_by_dedupe_key(self, dedupe_key: str) -> GatewayOperation | None:
        pass

    @abstractmethod
    async def list_by_status(self, status: GatewayOperationStatus) -> list[GatewayOperation]:
        pass

    @abstractmethod
    async def save(self, operation: GatewayOperation) -> GatewayOperation:
        pass
