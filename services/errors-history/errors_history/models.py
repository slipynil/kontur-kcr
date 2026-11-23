from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_BULK_SIZE = 1000
MAX_PAGE = 1_000_000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

# PostgreSQL TEXT supports the original Excel text, including whitespace, except NUL.
TextValue = Annotated[str, StringConstraints(pattern=r"^[^\x00]*$")]


class ImportType(StrEnum):
    REGISTRY = "REGISTRY"
    SNAPSHOT = "SNAPSHOT"


class ErrorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_error_id: Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
    batch_id: UUID
    import_type: ImportType
    kcr_code: TextValue | None = None
    sheet: TextValue
    row_number: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    reason: TextValue
    recorded_at: AwareDatetime | None

    @field_validator("recorded_at", mode="before")
    @classmethod
    def parse_recorded_at(cls, value: object) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("recorded_at must be an ISO 8601 timestamp or null")


class ErrorBulk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errors: Annotated[list[ErrorInput], Field(min_length=1, max_length=MAX_BULK_SIZE)]


class ErrorItem(ErrorInput):
    id: UUID
    kcr_code: TextValue | None


class ErrorBulkResult(BaseModel):
    inserted: int
    existing: int


class ErrorPage(BaseModel):
    items: list[ErrorItem]
    page: int
    page_size: int
    has_more: bool
