from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

KcrCode = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00]+$")]
QuarterValue = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}_Q[1-4]$")]
Stage = Literal["POOL", "IN_WORK", "WAIT_DEADLINE", "CLAIM", "CLOSED"]


class SnapshotDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: UUID
    part_number: Annotated[int, Field(strict=True, ge=1, le=1000)]
    part_count: Annotated[int, Field(strict=True, ge=2, le=1000)]

    @model_validator(mode="after")
    def validate_part_number(self):
        if self.part_number > self.part_count:
            raise ValueError("Номер части превышает количество частей")
        return self


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    kcr_code: KcrCode


class ClientCleanupRequest(CleanupRequest):
    scope: Literal["CLIENT"]


class TargetedCleanupRequest(CleanupRequest):
    cycle_ids: Annotated[list[UUID], Field(max_length=100)] = Field(default_factory=list)
    event_ids: Annotated[list[UUID], Field(max_length=5000)] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_targets(self):
        if not self.cycle_ids and not self.event_ids:
            raise ValueError("Укажите циклы или события для очистки")
        if len(set(self.cycle_ids)) != len(self.cycle_ids) or len(set(self.event_ids)) != len(
            self.event_ids
        ):
            raise ValueError("ID целей очистки должны быть уникальны")
        return self


class QuarterCleanupRequest(TargetedCleanupRequest):
    scope: Literal["QUARTER"]
    quarter: QuarterValue


class RestoreCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: UUID
    published: Annotated[bool, Field(strict=True)]
    stage: Literal["POOL", "IN_WORK", "WAIT_DEADLINE"]
    operator_id: UUID | None
    wait_until: date | None
    wait_source_work_quarter: QuarterValue | None = None

    @model_validator(mode="after")
    def validate_state(self):
        if self.stage == "WAIT_DEADLINE":
            if self.wait_until is None or self.operator_id is None:
                raise ValueError("WAIT_DEADLINE требует ответственного и дату")
        elif self.wait_until is not None or self.wait_source_work_quarter is not None:
            raise ValueError("Срок задаётся только для WAIT_DEADLINE")
        if self.stage == "POOL" and self.operator_id is not None:
            raise ValueError("Цикл в POOL не должен иметь ответственного")
        if self.stage == "IN_WORK" and self.operator_id is None:
            raise ValueError("IN_WORK требует ответственного")
        return self


class BatchCleanupRequest(TargetedCleanupRequest):
    scope: Literal["BATCH"]
    batch_id: UUID
    restore: RestoreCycle | None = None


CleanupBody = Annotated[
    ClientCleanupRequest | QuarterCleanupRequest | BatchCleanupRequest, Field(discriminator="scope")
]


class PoolItem(BaseModel):
    kcr_code: str
    assigned_operator_id: UUID | None


class PoolResponse(BaseModel):
    items: list[PoolItem]


class AssignmentState(PoolItem):
    work_quarter: str
    assignment_id: UUID | None
    assigned_at: datetime | None
    stage: Stage | None
    wait_until: date | None
    awaiting_balance: bool


class CycleInfo(BaseModel):
    cycle_id: UUID
    work_quarter: str
    is_current: bool
    published_at: datetime | None
    source_import_batch_id: UUID | None
    assigned_operator_id: UUID | None
    stage: Stage | None
    wait_until: date | None
    wait_source_work_quarter: QuarterValue | None


class CycleList(BaseModel):
    items: list[CycleInfo]


class ClientEvent(BaseModel):
    id: UUID
    kcr_code: str
    work_quarter: str | None
    author_id: UUID
    event_type: str
    call_result: str | None
    comment_text: str
    promised_date: date | None
    wait_until: date | None
    stage: Stage | None
    historical_event_date: datetime | None
    created_at: datetime
    document_quarters: list[QuarterValue]


class EventListResponse(BaseModel):
    items: list[ClientEvent]
    total: int
    page: int
    page_size: int
