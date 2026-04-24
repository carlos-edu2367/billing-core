from enum import Enum


class InternalWebhookDeliveryStatus(Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    DELIVERED = "delivered"
    FAILED = "failed"
