from enum import Enum

class SubscriptionStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CANCELED = "canceled"