from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.errors import NotFoundError
from app.infra.db.models.gateway_operation import GatewayOperationModel


class GatewayOperationRepositoryINFRA(GatewayOperationRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_dedupe_key(self, dedupe_key: str) -> GatewayOperation | None:
        stmt = select(GatewayOperationModel).where(GatewayOperationModel.dedupe_key == dedupe_key)
        result = await self.session.execute(stmt)
        record = result.scalars().one_or_none()
        return record.to_domain() if record else None

    async def save(self, operation: GatewayOperation) -> GatewayOperation:
        if not operation.id:
            new = GatewayOperationModel(
                operation_name=operation.operation_name,
                dedupe_key=operation.dedupe_key,
                provider=operation.provider,
                system=operation.system,
                request_payload=operation.request_payload,
                status=operation.status,
                gateway_reference=operation.gateway_reference,
                error_message=operation.error_message,
                created_at=operation.created_at,
                updated_at=operation.updated_at,
            )
            self.session.add(new)
            await self.session.flush()
            return new.to_domain()

        record = await self.session.get(GatewayOperationModel, operation.id)
        if not record:
            raise NotFoundError("Gateway Operation Not Found")

        record.status = operation.status
        record.gateway_reference = operation.gateway_reference
        record.error_message = operation.error_message
        record.updated_at = operation.updated_at
        await self.session.flush()
        return record.to_domain()
