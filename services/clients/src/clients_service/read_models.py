from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from clients_service.import_models import ClientStatus, Quarter, Tag


class Dynamics(StrEnum):
    REDUCING = "REDUCING"
    NO_CHANGE = "NO_CHANGE"
    GROWING = "GROWING"
    ALL_SUBMITTED = "ALL_SUBMITTED"


class Duration(StrEnum):
    FIRST_TIME = "FIRST_TIME"
    REPEATED = "REPEATED"
    LONG_DELAY_2Q = "LONG_DELAY_2Q"


class Snapshot(BaseModel):
    kcr_code: str
    quarter: Quarter
    last_observation_quarter: Quarter
    total_documents: int
    unsubmitted_documents: int
    import_batch_id: UUID
    snapshot_date: date


class QuarterSummary(BaseModel):
    quarter: Quarter
    total_documents: int
    unsubmitted_documents: int
    unsubmitted_percent: float | None


class ClientListItem(BaseModel):
    kcr_code: str
    inn: str | None
    organization_name: str | None
    status: ClientStatus
    tags: list[Tag]
    needs_review: bool
    inherited_fields: dict[str, Any]
    dynamics: Dynamics | None
    dynamics_quarter: Quarter
    dynamics_previous_quarter: Quarter
    dynamics_current_documents: int | None
    dynamics_previous_documents: int | None
    duration: Duration | None
    outstanding_documents: int
    latest_snapshot: Snapshot | None
    quarter_summary: QuarterSummary | None


class ClientPage(BaseModel):
    items: list[ClientListItem]
    page: int
    page_size: int
    total: int


class ClientCard(BaseModel):
    kcr_code: str
    inn: str | None
    kpp: str | None
    organization_name: str | None
    status: ClientStatus
    is_blocked: bool
    manager_name: str | None
    contacts: dict[str, Any] | None
    tags: list[Tag]
    needs_review: bool
    inherited_fields: dict[str, Any]
    snapshots: list[Snapshot]
    dynamics: Dynamics | None
    dynamics_quarter: Quarter
    dynamics_previous_quarter: Quarter
    dynamics_current_documents: int | None
    dynamics_previous_documents: int | None
    duration: Duration | None
    outstanding_documents: int
    quarter_summary: QuarterSummary | None


class HistoryRecord(BaseModel):
    id: UUID
    kcr_code: str
    quarter: Quarter
    observation_quarter: Quarter
    unsubmitted_documents: int
    import_batch_id: UUID
    snapshot_date: date
    created_at: datetime
    observation_order: int
    inherited_fields: dict[str, Any]


class HistoryPage(BaseModel):
    items: list[HistoryRecord]
    total: int
    limit: int
    offset: int
