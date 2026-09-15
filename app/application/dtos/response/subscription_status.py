from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GetSubscriptionStatusResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "subscription_id": "018f2b2e-6e2a-7c2e-9a2e-2b2e6e2a7c2e",
                "gateway_status": "ACTIVE",
                "next_due_date": "2026-11-01",
                "value": "129.90",
                "cycle": "MONTHLY",
            }
        }
    )

    subscription_id: UUID
    gateway_status: str
    next_due_date: date
    value: Decimal
    cycle: str
