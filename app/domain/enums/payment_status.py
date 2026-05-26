from enum import Enum

class PaymentStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PAID = "paid"
    OVERDUE = "overdue"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REFUNDED = "refunded"
