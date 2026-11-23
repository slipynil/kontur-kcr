from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from client_history.models import (
    AssignmentKind,
    EventType,
    HistoryEventBase,
    HistoryEventOut,
    Source,
    Stage,
    StageReason,
)


class Base(DeclarativeBase):
    pass


class HistoryEventRow(Base):
    __tablename__ = "history_events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kcr_code: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[UUID | None]
    actor_name: Mapped[str | None] = mapped_column(Text)
    work_quarter: Mapped[str | None] = mapped_column(Text)
    cycle_id: Mapped[UUID | None]
    document_quarters: Mapped[list[str]] = mapped_column(ARRAY(Text))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Assigned by the database: the order of events that share a timestamp.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(Text)


class HistoryCleanupRow(Base):
    __tablename__ = "history_cleanup_operations"

    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    kcr_code: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    deleted: Mapped[int]
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def row_values(source: Source, event: HistoryEventBase, fingerprint: str) -> dict:
    payload = event.payload
    return {
        "id": event.id,
        "kcr_code": event.kcr_code,
        "event_type": event.event_type.value,
        "source": source.value,
        "actor_id": event.actor_id,
        "actor_name": event.actor_name,
        "work_quarter": event.work_quarter,
        "cycle_id": event.cycle_id,
        "document_quarters": event.document_quarters,
        "occurred_at": event.occurred_at,
        "recorded_at": event.recorded_at,
        "payload": payload if isinstance(payload, dict) else payload.model_dump(mode="json"),
        "fingerprint": fingerprint,
    }


ASSIGN_TITLES = {
    AssignmentKind.TAKE: "Клиент взят в работу",
    AssignmentKind.REASSIGN: "Клиент перезакреплён",
    AssignmentKind.INHERITED: "Ожидание перенесено в новый цикл",
}
STAGE_TITLES = {
    StageReason.TAKE: "Клиент взят в работу",
    StageReason.SNAPSHOT: "Создан цикл обзвона по срезу",
    StageReason.WAIT_SET: "Установлен срок ожидания",
    StageReason.WAIT_EXPIRED: "Срок ожидания истёк. Клиент возвращён в пул",
    StageReason.RESTORE: "Состояние цикла восстановлено после отката",
}


def title(event_type: EventType, payload: dict) -> str:
    """The heading of a system record, as the Operator Work log used to word it."""
    if event_type is EventType.OPERATOR_ASSIGN:
        return ASSIGN_TITLES[AssignmentKind(payload["kind"])]
    if event_type is EventType.STAGE_CHANGE:
        return STAGE_TITLES[StageReason(payload["reason"])]
    if event_type is EventType.PUBLICATION:
        return "Клиент передан в обзвон"
    return ""


def to_event(row: HistoryEventRow) -> HistoryEventOut:
    event_type = EventType(row.event_type)
    payload = row.payload or {}
    stage = payload.get("to_stage")
    if stage is None and event_type is EventType.CALL_RESULT and payload.get("wait_until"):
        stage = Stage.WAIT_DEADLINE
    return HistoryEventOut(
        id=row.id,
        kcr_code=row.kcr_code,
        event_type=event_type,
        source=Source(row.source),
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        work_quarter=row.work_quarter,
        cycle_id=row.cycle_id,
        document_quarters=row.document_quarters,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        seq=row.seq,
        payload=payload,
        author_id=row.actor_id or UUID(int=0),
        comment_text=payload.get("comment_text", title(event_type, payload)),
        created_at=row.recorded_at,
        historical_event_date=(
            row.occurred_at if event_type is EventType.HISTORICAL_COMMENT else None
        ),
        call_result=payload.get("call_result"),
        promised_date=payload.get("promised_date"),
        wait_until=payload.get("wait_until"),
        stage=stage,
    )
