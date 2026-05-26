from enum import Enum

class SubscriptionStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CANCELLATION_PENDING = "cancellation_pending"
    CANCELED = "canceled"
