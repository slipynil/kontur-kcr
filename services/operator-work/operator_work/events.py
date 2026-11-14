from datetime import date
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import Engine, text

from operator_work.assignments import (
    BUSINESS_ZONE,
    WorkError,
    _active_assignment,
    _current_cycle,
    _lock,
    _one,
    _record_stage,
    _require_cycle,
    _settle_overdue,
)
from operator_work.contracts import QuarterValue


class EventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    document_quarters: Annotated[list[QuarterValue], Field(max_length=100)] = Field(
        default_factory=list
    )

    @field_validator("document_quarters")
    @classmethod
    def validate_document_quarters(cls, quarters: list[str]) -> list[str]:
        if len(set(quarters)) != len(quarters):
            raise ValueError("Исходные кварталы документов должны быть уникальны")
        return sorted(quarters)


class CallRequest(EventRequest):
    event_type: Literal["CALL_RESULT"]
    expected_assignment_id: UUID
    work_quarter: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}_Q[1-4]$")]
    call_result: Literal["AGREEMENT", "REFUSAL", "NO_ANSWER", "WRONG_NUMBER"]
    comment_text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""
    promised_date: date | None = None
    wait_until: date | None = None


class HistoricalCommentRequest(EventRequest):
    event_type: Literal["HISTORICAL_COMMENT"]
    work_quarter: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}_Q[1-4]$")] | None = None
    comment_text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    historical_event_date: AwareDatetime


def _replay(db, params):
    existing = _one(db, "SELECT * FROM client_events WHERE id = :id", id=params["id"])
    if existing is not None and any(existing[key] != value for key, value in params.items()):
        raise WorkError(409, "REQUEST_ID_CONFLICT", "ID запроса уже используется")
    return existing


def _event_result(row) -> dict:
    result = dict(row)
    result.pop("requested_wait_until")
    result.pop("call_assignment_id")
    return result


class ClientEvents:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list(self, code: str, quarter: str | None, page: int, size: int, role: str) -> dict:
        with self.engine.connect() as db:
            cycle = _current_cycle(db, code)
            if role == "OPERATOR" and (cycle is None or cycle["published_at"] is None):
                raise WorkError(404, "CLIENT_NOT_PUBLISHED", "Клиент недоступен")
            if cycle is None and not db.scalar(
                text("""SELECT EXISTS (SELECT 1 FROM client_work_cycles WHERE kcr_code = :code)
                            OR EXISTS (SELECT 1 FROM client_events WHERE kcr_code = :code)"""),
                {"code": code},
            ):
                raise WorkError(404, "CLIENT_NOT_PUBLISHED", "Клиент недоступен")
            params = {"code": code, "quarter": quarter, "size": size, "skip": (page - 1) * size}
            where = """WHERE kcr_code = :code
                         AND (CAST(:quarter AS text) IS NULL OR work_quarter = :quarter)"""
            total = db.scalar(text("SELECT count(*) FROM client_events " + where), params)
            rows = db.execute(
                text(
                    "SELECT id, kcr_code, work_quarter, author_id, event_type, call_result, "
                    "comment_text, promised_date, wait_until, stage, historical_event_date, "
                    "created_at, document_quarters FROM client_events "
                    + where
                    + " ORDER BY created_at DESC, id DESC LIMIT :size OFFSET :skip"
                ),
                params,
            ).mappings()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": size,
            }

    def save_historical_comment(
        self, code: str, operator: UUID, comment: HistoricalCommentRequest
    ) -> tuple[int, dict]:
        with self.engine.begin() as db:
            now = _lock(db, code)
            params = {
                "id": comment.request_id,
                "kcr_code": code,
                "author_id": operator,
                "event_type": comment.event_type,
                "work_quarter": comment.work_quarter,
                "comment_text": comment.comment_text,
                "document_quarters": comment.document_quarters,
                "historical_event_date": comment.historical_event_date,
            }
            existing = _replay(db, params)
            if existing is not None:
                return 200, _event_result(existing)

            cycle = _current_cycle(db, code)
            if cycle is None or cycle["published_at"] is None:
                raise WorkError(404, "CLIENT_NOT_PUBLISHED", "Клиент недоступен")
            if comment.work_quarter is not None and comment.work_quarter != cycle["work_quarter"]:
                raise WorkError(409, "INVALID_STATE", "Цикл обзвона изменился")
            if comment.historical_event_date >= now:
                raise WorkError(422, "INVALID_EVENT_DATE", "Укажите дату и время события в прошлом")

            row = _one(
                db,
                """INSERT INTO client_events
                       (id, kcr_code, author_id, event_type, work_quarter, comment_text,
                        historical_event_date, created_at, document_quarters)
                   VALUES (:id, :kcr_code, :author_id, :event_type, :work_quarter, :comment_text,
                           :historical_event_date, :now, CAST(:document_quarters AS text[]))
                   ON CONFLICT (id) DO NOTHING RETURNING *""",
                **params,
                now=now,
            )
            if row is None:
                raise WorkError(409, "REQUEST_ID_CONFLICT", "ID запроса уже используется")
            return 201, _event_result(row)

    def save_call(self, code: str, operator: UUID, call: CallRequest) -> tuple[int, dict]:
        with self.engine.begin() as db:
            now = _lock(db, code)
            params = {
                "id": call.request_id,
                "kcr_code": code,
                "author_id": operator,
                "work_quarter": call.work_quarter,
                "call_assignment_id": call.expected_assignment_id,
                "event_type": call.event_type,
                "call_result": call.call_result,
                "comment_text": call.comment_text,
                "document_quarters": call.document_quarters,
                "promised_date": call.promised_date,
                "requested_wait_until": call.wait_until,
            }
            existing = _replay(db, params)
            if existing is not None:
                return 200, self._call_result(existing)

            quarter = call.work_quarter
            _require_cycle(db, code, quarter)
            original = _one(
                db,
                """SELECT id FROM client_assignments
                   WHERE id = :id AND kcr_code = :code AND work_quarter = :quarter
                     AND operator_id = :operator""",
                id=call.expected_assignment_id,
                code=code,
                quarter=quarter,
                operator=operator,
            )
            if original is None:
                raise WorkError(409, "ASSIGNMENT_CHANGED", "У вас не было этого назначения")

            _settle_overdue(db, code, quarter, now)
            active = _active_assignment(db, code, quarter)
            wait = call.wait_until if active and active["id"] == original["id"] else None
            if wait is not None and wait <= now.astimezone(BUSINESS_ZONE).date():
                raise WorkError(422, "INVALID_WAIT_DATE", "Укажите будущую дату ожидания")

            row = _one(
                db,
                """INSERT INTO client_events
                       (id, kcr_code, work_quarter, author_id, event_type, call_result,
                        comment_text, promised_date, wait_until, stage, created_at,
                        call_assignment_id, requested_wait_until, document_quarters)
                   VALUES (:id, :kcr_code, :work_quarter, :author_id, :event_type, :call_result,
                           :comment_text, :promised_date, :wait_until, :stage, :now,
                           :call_assignment_id, :requested_wait_until, CAST(:document_quarters AS text[]))
                   ON CONFLICT (id) DO NOTHING RETURNING *""",
                **params,
                wait_until=wait,
                stage="WAIT_DEADLINE" if wait else None,
                now=now,
            )
            if row is None:
                raise WorkError(409, "REQUEST_ID_CONFLICT", "ID запроса уже используется")
            if wait is not None:
                self._set_wait(db, code, quarter, operator, wait, now)
            return 201, self._call_result(row)

    @staticmethod
    def _call_result(row) -> dict:
        result = _event_result(row)
        result["wait_applied"] = (
            row["requested_wait_until"] is not None and row["wait_until"] is not None
        )
        return result

    @staticmethod
    def _set_wait(db, code, quarter, operator, wait, now):
        params = {"code": code, "quarter": quarter, "operator": operator, "wait": wait, "now": now}
        db.execute(
            text(
                """UPDATE client_work_cycles
                   SET wait_until = :wait, wait_source_work_quarter = NULL
                   WHERE kcr_code = :code AND work_quarter = :quarter"""
            ),
            params,
        )
        _record_stage(db, code, quarter, operator, "WAIT_DEADLINE", now)
        db.execute(
            text(
                """INSERT INTO client_events
                       (id, kcr_code, work_quarter, author_id, event_type, comment_text,
                        stage, wait_until, created_at)
                   VALUES (:id, :code, :quarter, :operator, 'STAGE_CHANGE',
                           'Установлен срок ожидания', 'WAIT_DEADLINE', :wait, :now)"""
            ),
            {**params, "id": uuid4()},
        )
        db.execute(
            text(
                """UPDATE client_reassignment_requests
                   SET status = 'CANCELLED', resolved_at = :now
                   WHERE kcr_code = :code AND work_quarter = :quarter AND status = 'PENDING'"""
            ),
            params,
        )
