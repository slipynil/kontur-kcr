"""Moves the Operator Work event log written before ADR-0020 into the chronicle.

Read-only access to Operator Work DB and the ordinary ingest endpoint of Client History.
Repeating the run is safe: the service skips records it already knows. Records without a
known assignment or previous assignee keep those fields empty; the reason of a stage change
is taken from the wording of the old record.
"""

import argparse
import os
import time
from collections.abc import Iterator, Mapping
from itertools import batched
from uuid import UUID

import httpx
from sqlalchemy import Engine, create_engine, text

from client_history.models import (
    EVENT_MODELS,
    AssignmentKind,
    EventType,
    HistoryEventBase,
    Source,
    Stage,
    StageReason,
)

LEGACY_SQL = text("""
    SELECT e.id, e.kcr_code, e.work_quarter, e.author_id, e.event_type, e.call_result,
           e.comment_text, e.promised_date, e.wait_until, e.stage, e.historical_event_date,
           e.created_at, e.document_quarters, e.call_assignment_id, c.cycle_id
    FROM client_events AS e
    LEFT JOIN client_work_cycles AS c
           ON c.kcr_code = e.kcr_code AND c.work_quarter = e.work_quarter
    ORDER BY e.created_at, e.id
""")

# Operator Work records a rule-driven transition with this author (wait_expiry.py).
SYSTEM_ACTOR = UUID(int=0)
LEGACY_STAGE_REASONS = {
    "Установлен срок ожидания": StageReason.WAIT_SET,
    "Срок ожидания истёк. Клиент возвращён в пул": StageReason.WAIT_EXPIRED,
    "Состояние цикла восстановлено после отката": StageReason.RESTORE,
}


class BackfillError(Exception):
    pass


def stage_reason(row: Mapping) -> StageReason:
    known = LEGACY_STAGE_REASONS.get(row["comment_text"] or "")
    if known is not None:
        return known
    # Older wording: infer from the stage and whether a rule made the transition.
    if row["stage"] == Stage.WAIT_DEADLINE:
        return StageReason.WAIT_SET
    if row["stage"] == Stage.IN_WORK:
        return StageReason.TAKE
    if row["stage"] == Stage.POOL and row["author_id"] == SYSTEM_ACTOR:
        return StageReason.WAIT_EXPIRED
    return StageReason.RESTORE


def to_history(row: Mapping) -> HistoryEventBase:
    event_type = EventType(row["event_type"])
    author = row["author_id"]
    common = {
        "id": row["id"],
        "kcr_code": row["kcr_code"],
        "actor_id": None if author == SYSTEM_ACTOR else author,
        "work_quarter": row["work_quarter"],
        "cycle_id": row["cycle_id"],
        "document_quarters": row["document_quarters"] or [],
        "occurred_at": row["historical_event_date"] or row["created_at"],
        "recorded_at": row["created_at"],
    }
    if event_type is EventType.CALL_RESULT:
        payload = {
            "call_result": row["call_result"],
            "comment_text": row["comment_text"] or "",
            "promised_date": row["promised_date"],
            "wait_until": row["wait_until"],
            "assignment_id": row["call_assignment_id"],
        }
    elif event_type is EventType.HISTORICAL_COMMENT:
        payload = {"comment_text": row["comment_text"]}
    elif event_type is EventType.OPERATOR_ASSIGN:
        payload = {
            "operator_id": author,
            "kind": (
                AssignmentKind.REASSIGN
                if row["comment_text"] == "Клиент перезакреплён"
                else AssignmentKind.TAKE
            ),
        }
    elif event_type is EventType.STAGE_CHANGE:
        payload = {
            "to_stage": row["stage"],
            "reason": stage_reason(row),
            "wait_until": row["wait_until"],
        }
    else:
        payload = {}
    try:
        return EVENT_MODELS[event_type].model_validate(
            {"event_type": event_type, **common, "payload": payload}
        )
    except ValueError as exc:
        raise BackfillError(f"Запись {row['id']} не переносится: {exc}") from exc


def read_rows(engine: Engine) -> Iterator[Mapping]:
    with engine.connect() as connection:
        yield from connection.execute(LEGACY_SQL).mappings()


def post(client: httpx.Client, events: list[HistoryEventBase], attempts: int, delay: float) -> dict:
    body = {
        "source": Source.OPERATOR_WORK.value,
        "events": [event.model_dump(mode="json") for event in events],
    }
    for attempt in range(attempts):
        try:
            response = client.post("/internal/v1/history/events", json=body)
        except httpx.HTTPError:
            response = None
        if response is not None:
            if response.status_code == 200:
                return response.json()
            if response.status_code in {409, 422}:
                ids = ", ".join(str(event.id) for event in events)
                raise BackfillError(
                    f"Client History отклонил пакет с записями {ids}: "
                    f"{response.status_code} {response.text}"
                )
        if attempt + 1 < attempts:
            time.sleep(delay * (attempt + 1))
    raise BackfillError("Client History не принял пакет; повторите перенос")


def run(engine: Engine, client: httpx.Client, batch_size: int, attempts: int = 5) -> dict:
    counters = {"rows": 0, "accepted": 0, "duplicates": 0}
    for chunk in batched(map(to_history, read_rows(engine)), batch_size, strict=False):
        result = post(client, list(chunk), attempts, 3.0)
        counters["rows"] += len(chunk)
        counters["accepted"] += result["accepted"]
        counters["duplicates"] += result["duplicates"]
    return counters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operator-work-database-url",
        default=os.environ.get("BACKFILL_OPERATOR_WORK_DATABASE_URL"),
        help="Read-only URL of Operator Work DB",
    )
    parser.add_argument(
        "--client-history-url",
        default=os.environ.get("BACKFILL_CLIENT_HISTORY_URL"),
        help="Base URL of Client History, for example http://127.0.0.1:8005",
    )
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BACKFILL_BATCH_SIZE", 500))
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if not args.operator_work_database_url or not args.client_history_url:
        parser.error("Укажите URL Operator Work DB и базовый URL Client History")
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size должен быть от 1 до 1000")
    engine = create_engine(args.operator_work_database_url, hide_parameters=True)
    try:
        with httpx.Client(
            base_url=args.client_history_url, timeout=args.timeout, trust_env=False
        ) as client:
            counters = run(engine, client, args.batch_size)
    finally:
        engine.dispose()
    print(
        f"Записей прочитано: {counters['rows']}; "
        f"принято: {counters['accepted']}; повторено: {counters['duplicates']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
