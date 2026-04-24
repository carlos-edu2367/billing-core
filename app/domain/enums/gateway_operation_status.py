from enum import Enum


class GatewayOperationStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUIRES_RECONCILIATION = "requires_reconciliation"
