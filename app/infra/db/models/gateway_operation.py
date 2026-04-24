from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.infra.db.setup import Base
from app.infra.db.types.enum_value_type import EnumValueType


class GatewayOperationModel(Base):
    __tablename__ = "gateway_operations"
    __table_args__ = (
        Index("ix_gateway_operations_system_status_created", "system", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    operation_name: Mapped[str] = mapped_column(String(100), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider: Mapped[GatewayProvider] = mapped_column(EnumValueType(GatewayProvider), nullable=False, index=True)
    system: Mapped[System] = mapped_column(EnumValueType(System), nullable=False, index=True)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[GatewayOperationStatus] = mapped_column(EnumValueType(GatewayOperationStatus), nullable=False, index=True)
    gateway_reference: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_domain(self) -> GatewayOperation:
        return GatewayOperation(
            operation_name=self.operation_name,
            dedupe_key=self.dedupe_key,
            provider=self.provider,
            system=self.system,
            request_payload=self.request_payload,
            status=self.status,
            gateway_reference=self.gateway_reference,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            id=self.id,
        )
