import hashlib
import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

QUARTER_PATTERN = r"^[0-9]{4}_Q[1-4]$"
Quarter = Annotated[str, Field(pattern=QUARTER_PATTERN)]
KcrCode = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00]+$")]


class EventType(StrEnum):
    CALL_RESULT = "CALL_RESULT"
    HISTORICAL_COMMENT = "HISTORICAL_COMMENT"
    COMMENT = "COMMENT"
    OPERATOR_ASSIGN = "OPERATOR_ASSIGN"
    PUBLICATION = "PUBLICATION"
    STAGE_CHANGE = "STAGE_CHANGE"
    EMAIL_SENT = "EMAIL_SENT"
    CLAIM_UPDATE = "CLAIM_UPDATE"


class Source(StrEnum):
    OPERATOR_WORK = "OPERATOR_WORK"


class Stage(StrEnum):
    POOL = "POOL"
    IN_WORK = "IN_WORK"
    WAIT_DEADLINE = "WAIT_DEADLINE"
    CLAIM = "CLAIM"
    CLOSED = "CLOSED"


class CallResult(StrEnum):
    AGREEMENT = "AGREEMENT"
    REFUSAL = "REFUSAL"
    NO_ANSWER = "NO_ANSWER"
    WRONG_NUMBER = "WRONG_NUMBER"


class AssignmentKind(StrEnum):
    TAKE = "TAKE"
    REASSIGN = "REASSIGN"
    INHERITED = "INHERITED"


class StageReason(StrEnum):
    TAKE = "TAKE"
    SNAPSHOT = "SNAPSHOT"
    WAIT_SET = "WAIT_SET"
    WAIT_EXPIRED = "WAIT_EXPIRED"
    RESTORE = "RESTORE"


class CleanupScope(StrEnum):
    CLIENT = "CLIENT"
    TARGETED = "TARGETED"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallPayload(Payload):
    call_result: CallResult
    comment_text: str = Field(max_length=4000)
    promised_date: date | None = None
    wait_until: date | None = None
    assignment_id: UUID | None = None


class CommentPayload(Payload):
    comment_text: str = Field(min_length=1, max_length=4000)


class AssignmentPayload(Payload):
    operator_id: UUID
    kind: AssignmentKind
    previous_operator_id: UUID | None = None
    # Stays empty for records moved from the Operator Work log before ADR-0020.
    assignment_id: UUID | None = None


class PublicationPayload(Payload):
    pass


class StageChangePayload(Payload):
    to_stage: Stage
    reason: StageReason
    from_stage: Stage | None = None
    wait_until: date | None = None
    import_batch_id: UUID | None = None


# EMAIL_SENT and CLAIM_UPDATE are reserved: their details are agreed with UC-14–19.
ReservedPayload = dict[str, Any]


class HistoryEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    kcr_code: KcrCode
    actor_id: UUID | None = None
    actor_name: str | None = Field(default=None, max_length=255)
    work_quarter: Quarter | None = None
    cycle_id: UUID | None = None
    document_quarters: list[Quarter] = Field(default_factory=list, max_length=100)
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime

    @field_validator("document_quarters")
    @classmethod
    def sort_unique_quarters(cls, quarters: list[str]) -> list[str]:
        if len(set(quarters)) != len(quarters):
            raise ValueError("Исходные кварталы документов должны быть уникальны")
        return sorted(quarters)

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def to_utc(cls, moment: datetime) -> datetime:
        # One representation for equal instants keeps fingerprints comparable.
        return moment.astimezone(UTC)


class CallHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.CALL_RESULT]
    work_quarter: Quarter
    payload: CallPayload


class HistoricalCommentHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.HISTORICAL_COMMENT]
    payload: CommentPayload


class CommentHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.COMMENT]
    work_quarter: Quarter
    payload: CommentPayload


class AssignmentHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.OPERATOR_ASSIGN]
    work_quarter: Quarter
    payload: AssignmentPayload


class PublicationHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.PUBLICATION]
    work_quarter: Quarter
    payload: PublicationPayload


class StageChangeHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.STAGE_CHANGE]
    work_quarter: Quarter
    payload: StageChangePayload


class ReservedHistoryEvent(HistoryEventBase):
    event_type: Literal[EventType.EMAIL_SENT, EventType.CLAIM_UPDATE]
    work_quarter: Quarter
    payload: ReservedPayload


HistoryEvent = Annotated[
    CallHistoryEvent
    | HistoricalCommentHistoryEvent
    | CommentHistoryEvent
    | AssignmentHistoryEvent
    | PublicationHistoryEvent
    | StageChangeHistoryEvent
    | ReservedHistoryEvent,
    Field(discriminator="event_type"),
]

EVENT_MODELS: dict[str, type[HistoryEventBase]] = {
    EventType.CALL_RESULT: CallHistoryEvent,
    EventType.HISTORICAL_COMMENT: HistoricalCommentHistoryEvent,
    EventType.COMMENT: CommentHistoryEvent,
    EventType.OPERATOR_ASSIGN: AssignmentHistoryEvent,
    EventType.PUBLICATION: PublicationHistoryEvent,
    EventType.STAGE_CHANGE: StageChangeHistoryEvent,
    EventType.EMAIL_SENT: ReservedHistoryEvent,
    EventType.CLAIM_UPDATE: ReservedHistoryEvent,
}


def _digest(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def fingerprint(event: HistoryEventBase) -> str:
    """Identifies the content a source sent; a name added by the service is not part of it."""
    data = event.model_dump(mode="json")
    if data["actor_name"] is None:
        del data["actor_name"]
    return _digest(data)


class HistoryEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    events: Annotated[list[HistoryEvent], Field(min_length=1, max_length=1000)]


class IngestResult(BaseModel):
    accepted: int
    duplicates: int


class HistoryCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    kcr_code: KcrCode
    scope: CleanupScope
    event_ids: list[UUID] = Field(default_factory=list, max_length=5000)
    cycle_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("event_ids", "cycle_ids")
    @classmethod
    def unique_targets(cls, targets: list[UUID]) -> list[UUID]:
        if len(set(targets)) != len(targets):
            raise ValueError("ID целей очистки должны быть уникальны")
        return targets

    @model_validator(mode="after")
    def targeted_scope_has_targets(self):
        if self.scope is CleanupScope.TARGETED and not (self.event_ids or self.cycle_ids):
            raise ValueError("Укажите события или циклы для выборочной очистки")
        return self

    def fingerprint(self) -> str:
        data = self.model_dump(mode="json")
        data["event_ids"].sort()
        data["cycle_ids"].sort()
        return _digest(data)


class HistoryCleanupResult(BaseModel):
    operation_id: UUID
    kcr_code: KcrCode
    scope: CleanupScope
    deleted: int


class HistoryEventOut(BaseModel):
    """A stored record as the chronicle reads it, with the fields the service assigns."""

    id: UUID
    kcr_code: KcrCode
    event_type: EventType
    source: Source
    actor_id: UUID | None
    actor_name: str | None
    work_quarter: Quarter | None
    cycle_id: UUID | None
    document_quarters: list[Quarter]
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    seq: int
    payload: dict[str, Any]
    # Compatibility fields of the public contract (public-api.yaml), derived from the record.
    author_id: UUID
    comment_text: str
    created_at: AwareDatetime
    historical_event_date: AwareDatetime | None
    call_result: CallResult | None
    promised_date: date | None
    wait_until: date | None
    stage: Stage | None


class HistoryEventListResponse(BaseModel):
    items: list[HistoryEventOut]
    total: int
    page: int
    page_size: int
