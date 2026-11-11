from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

QUARTER_PATTERN = r"^[0-9]{4}_Q[1-4]$"
Quarter = Annotated[str, Field(pattern=QUARTER_PATTERN)]
Counter = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]


class ImportType(StrEnum):
    REGISTRY = "REGISTRY"
    SNAPSHOT = "SNAPSHOT"


class Status(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class ImportParameters(BaseModel):
    selected_quarter: Quarter
    import_type: ImportType
    snapshot_date: date


class ImportErrorRow(BaseModel):
    sheet: str
    row_number: int
    kcr_code: str | None = None
    reason: str


class Batch(ImportParameters):
    id: UUID
    uploaded_by: UUID
    filename: str
    status: Status
    total_rows: int
    success_count: int
    error_count: int
    created_at: datetime
    completed_at: datetime | None
    clients_applied_at: datetime | None
    error_log: list[ImportErrorRow] = Field(default_factory=list)


class ClientInput(BaseModel):
    kcr_code: str
    inn: str | None = None
    kpp: str | None = None
    organization_name: str | None = None
    status: str | None = None
    is_blocked: bool | None = None
    manager_name: str | None = None
    contacts: dict[str, str] | None = None
    tags: list[str] | None = None
    raw_work_status: str | None = None


class SnapshotInput(BaseModel):
    quarter: Quarter
    snapshot_date: date
    total_documents: Counter | None
    unsubmitted_documents: Counter | None


class ImportItem(BaseModel):
    client: ClientInput
    snapshot: SnapshotInput | None = None


class SourceRow(BaseModel):
    sheet: str
    row_number: int
    item: ImportItem

    def error(self, reason: str) -> ImportErrorRow:
        return ImportErrorRow(
            sheet=self.sheet,
            row_number=self.row_number,
            kcr_code=self.item.client.kcr_code,
            reason=reason,
        )


class ParsedImport(BaseModel):
    rows: list[SourceRow] = Field(default_factory=list)
    errors: list[ImportErrorRow] = Field(default_factory=list)
    total_rows: int = 0
