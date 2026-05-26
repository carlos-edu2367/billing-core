from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums.system import System


class CancelSubscriptionDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    subscription_id: UUID
    system: System
    reason: str | None = None
    job_id: str | None = None
