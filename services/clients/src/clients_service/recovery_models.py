from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    requested_by: UUID


class RollbackConfirmation(BaseModel):
    batch_id: UUID
    rollback_confirmed_at: datetime
