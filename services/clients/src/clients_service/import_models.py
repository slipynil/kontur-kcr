"""Validated normalized input from Import; no Excel parsing or operator state."""

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

Quarter = Annotated[str, Field(pattern=r"^\d{4}_Q[1-4]$")]
CalendarQuarter = Annotated[
    str, Field(max_length=7, pattern=re.compile(r"^(?!0000)[0-9]{4}_Q[1-4]$"))
]
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


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientInput(InputModel):
    kcr_code: Annotated[str, Field(min_length=1)]
    inn: str | None = None
    kpp: str | None = None
    organization_name: str | None = None
    tags: list[Tag] | None = None
    manager_name: str | None = None
    contacts: dict[str, Any] | None = None
    raw_work_status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_profile_fields(cls, value: Any) -> Any:
        obsolete = {"status", "is_blocked"}
        if isinstance(value, dict) and obsolete.intersection(value):
            return {key: field for key, field in value.items() if key not in obsolete}
        return value

    @field_validator("kcr_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("kcr_code cannot be blank")
        return value.strip()


class SnapshotInput(InputModel):
    quarter: CalendarQuarter
    total_documents: Counter | None = None
    unsubmitted_documents: Counter | None = None
    snapshot_date: date | None = None


class ImportItem(InputModel):
    client: ClientInput
    snapshot: SnapshotInput | None = None
    verify_only: StrictBool = False


class ImportEnvelope(InputModel):
    batch_id: UUID
    uploaded_by: UUID
    import_type: ImportType
    selected_quarter: CalendarQuarter | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    snapshot_date: date | None = Field(default=None, exclude_if=lambda value: value is None)
    # Row validation belongs to the application layer so one bad row need not
    # prevent validation of its neighbours. The envelope itself must be valid.
    items: Annotated[list[dict[str, Any]], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_snapshot_metadata(self) -> Self:
        if self.import_type is ImportType.SNAPSHOT:
            if self.selected_quarter is None or self.snapshot_date is None:
                raise ValueError("Snapshot imports require a quarter and date")
        elif self.model_fields_set & {"selected_quarter", "snapshot_date"}:
            raise ValueError("Registry imports do not take a quarter or date")
        return self
