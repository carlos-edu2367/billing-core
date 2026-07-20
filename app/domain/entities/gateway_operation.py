from datetime import datetime, timezone
from uuid import UUID

from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.domain.errors import DomainError


class GatewayOperation:
    def __init__(
        self,
        operation_name: str,
        dedupe_key: str,
        provider: GatewayProvider,
        system: System,
        request_payload: dict,
        status: GatewayOperationStatus = GatewayOperationStatus.PENDING,
        gateway_reference: str | None = None,
        error_message: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        id: UUID | None = None,
    ):
        normalized_operation_name = (operation_name or "").strip()
        normalized_dedupe_key = (dedupe_key or "").strip()

        if not normalized_operation_name:
            raise DomainError("GatewayOperation precisa ter operation_name.")

        if not normalized_dedupe_key:
            raise DomainError("GatewayOperation precisa ter dedupe_key.")

        self.id = id
        self.operation_name = normalized_operation_name
        self.dedupe_key = normalized_dedupe_key
        self.provider = provider
        self.system = system
        self.request_payload = request_payload
        self.status = status
        self.gateway_reference = gateway_reference
        self.error_message = error_message
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or self.created_at

    def mark_completed(self, gateway_reference: str) -> None:
        normalized_reference = (gateway_reference or "").strip()
        if not normalized_reference:
            raise DomainError("GatewayOperation precisa de gateway_reference ao concluir.")

        self.status = GatewayOperationStatus.COMPLETED
        self.gateway_reference = normalized_reference
        self.error_message = None
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, error_message: str) -> None:
        self.status = GatewayOperationStatus.FAILED
        self.error_message = (error_message or "").strip() or "Falha nao detalhada."
        self.updated_at = datetime.now(timezone.utc)

    def mark_requires_reconciliation(self, gateway_reference: str | None, error_message: str) -> None:
        normalized_reference = (gateway_reference or "").strip() or None
        self.status = GatewayOperationStatus.REQUIRES_RECONCILIATION
        self.gateway_reference = normalized_reference
        self.error_message = (error_message or "").strip() or "Operacao marcada para reconciliacao."
        self.updated_at = datetime.now(timezone.utc)
