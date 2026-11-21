from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

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
    import_type: ImportType
    selected_quarter: Quarter | None = None
    snapshot_date: date | None = None

    @model_validator(mode="after")
    def validate_snapshot_metadata(self) -> Self:
        if self.import_type is ImportType.SNAPSHOT:
            if self.selected_quarter is None or self.snapshot_date is None:
                raise ValueError("Snapshot imports require a quarter and date")
        elif self.selected_quarter is not None or self.snapshot_date is not None:
            raise ValueError("Registry imports do not take a quarter or date")
        return self

    @model_serializer(mode="wrap")
    def serialize_import(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.import_type is ImportType.REGISTRY:
            data.pop("selected_quarter", None)
            data.pop("snapshot_date", None)
        return data


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
    # Preserve the payload of legacy in-flight batches when retrying delivery.
    status: SkipJsonSchema[str | None] = None
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
    verify_only: bool = False

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
