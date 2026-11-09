"""Validated normalized input from Import; no Excel parsing or operator state."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

Quarter = Annotated[str, Field(pattern=r"^\d{4}_Q[1-4]$")]
Counter = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]


class ImportType(StrEnum):
    REGISTRY = "REGISTRY"
    SNAPSHOT = "SNAPSHOT"


class Tag(StrEnum):
    VIP = "VIP"
    NOVICE = "NOVICE"
    INTEGRATOR = "INTEGRATOR"
    TECH_PARTNER = "TECH_PARTNER"
    MPP = "MPP"
    DO_NOT_WORK = "DO_NOT_WORK"


class ClientStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientInput(InputModel):
    kcr_code: Annotated[str, Field(min_length=1)]
    inn: str | None = None
    kpp: str | None = None
    organization_name: str | None = None
    status: ClientStatus | None = None
    tags: list[Tag] | None = None
    manager_name: str | None = None
    is_blocked: StrictBool | None = None
    contacts: dict[str, Any] | None = None
    raw_work_status: str | None = None

    @field_validator("kcr_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("kcr_code cannot be blank")
        return value.strip()


class SnapshotInput(InputModel):
    quarter: Quarter
    total_documents: Counter | None = None
    unsubmitted_documents: Counter | None = None
    snapshot_date: date | None = None


class ImportItem(InputModel):
    client: ClientInput
    snapshot: SnapshotInput | None = None


class ImportEnvelope(InputModel):
    batch_id: UUID
    uploaded_by: UUID
    selected_quarter: Quarter
    import_type: ImportType
    snapshot_date: date
    # Row validation belongs to the application layer so one bad row need not
    # prevent validation of its neighbours. The envelope itself must be valid.
    items: Annotated[list[dict[str, Any]], Field(min_length=1)]
