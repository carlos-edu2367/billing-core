from enum import Enum

class PaymentStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REFUNDED = "refunded"