from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import NOW, upstream
from fastapi.testclient import TestClient
from sqlalchemy import text

from client_history import backfill
from client_history.api import create_app
from client_history.config import Settings
from client_history.models import AssignmentKind, StageReason

LEGACY_SCHEMA = """
CREATE TABLE client_work_cycles (kcr_code TEXT, work_quarter TEXT, cycle_id UUID);
CREATE TABLE client_events (
    id UUID PRIMARY KEY,
    kcr_code TEXT NOT NULL,
    work_quarter TEXT,
    author_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    call_result TEXT,
    comment_text TEXT NOT NULL,
    promised_date DATE,
    wait_until DATE,
    stage TEXT,
    historical_event_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    document_quarters TEXT[] NOT NULL DEFAULT '{}',
    call_assignment_id UUID
);
"""

INSERT_EVENT = text("""
    INSERT INTO client_events (id, kcr_code, work_quarter, author_id, event_type, call_result,
        comment_text, promised_date, wait_until, stage, historical_event_date, created_at,
        document_quarters, call_assignment_id)
    VALUES (:id, :kcr_code, :work_quarter, :author_id, :event_type, :call_result, :comment_text,
        :promised_date, :wait_until, :stage, :historical_event_date, :created_at,
        :document_quarters, :call_assignment_id)
""")


def legacy_row(event_type: str = "CALL_RESULT", **overrides) -> dict:
    row = {
        "id": uuid4(),
        "kcr_code": "6671-0042",
        "work_quarter": "2026_Q4",
        "author_id": uuid4(),
        "event_type": event_type,
        "call_result": "AGREEMENT" if event_type == "CALL_RESULT" else None,
        "comment_text": "Клиент взят в работу",
        "promised_date": None,
        "wait_until": None,
        "stage": None,
        "historical_event_date": None,
        "created_at": NOW,
        "document_quarters": [],
        "call_assignment_id": None,
        "cycle_id": None,
    }
    row.update(overrides)
    return row


def seed(engine, rows, cycle=None):
    with engine.begin() as connection:
        connection.execute(text(LEGACY_SCHEMA))
        if cycle is not None:
            connection.execute(
                text(
                    "INSERT INTO client_work_cycles (kcr_code, work_quarter, cycle_id)"
                    " VALUES (:kcr_code, :work_quarter, :cycle_id)"
                ),
                cycle,
            )
        for row in rows:
            connection.execute(INSERT_EVENT, row)


def gateway(request):
    ids = request.url.params["ids"].split(",")
    return httpx.Response(200, json={"items": [{"id": i, "login": f"user-{i[:8]}"} for i in ids]})


def handler_for(app_client):
    def handler(request):
        return app_client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={"content-type": "application/json"},
        )

    return handler


@contextmanager
def forwarding(engine, url):
    """An HTTP client whose requests reach the real application over ASGI."""
    app = create_app(Settings(url), engine=engine, gateway=upstream(gateway))
    with TestClient(app) as app_client, upstream(handler_for(app_client)) as client:
        yield client


def test_a_call_keeps_its_details():
    assignment = uuid4()
    promised = NOW.date() + timedelta(days=3)
    row = legacy_row(
        promised_date=promised,
        wait_until=promised,
        call_assignment_id=assignment,
        document_quarters=["2026_Q3"],
        comment_text="Договорились",
    )
    event = backfill.to_history(row)
    assert event.payload.call_result == "AGREEMENT"
    assert event.payload.assignment_id == assignment
    assert event.payload.promised_date == promised
    assert event.document_quarters == ["2026_Q3"]
    assert event.actor_id == row["author_id"]


def test_a_rule_driven_record_has_no_author():
    row = legacy_row(
        "STAGE_CHANGE",
        author_id=UUID(int=0),
        stage="POOL",
        comment_text="Срок ожидания истёк. Клиент возвращён в пул",
    )
    event = backfill.to_history(row)
    assert event.actor_id is None
    assert event.payload.reason is StageReason.WAIT_EXPIRED


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("Установлен срок ожидания", StageReason.WAIT_SET),
        ("Срок ожидания истёк. Клиент возвращён в пул", StageReason.WAIT_EXPIRED),
        ("Состояние цикла восстановлено после отката", StageReason.RESTORE),
    ],
)
def test_a_known_wording_gives_the_reason(comment, expected):
    event = backfill.to_history(
        legacy_row("STAGE_CHANGE", stage="WAIT_DEADLINE", comment_text=comment)
    )
    assert event.payload.reason is expected


@pytest.mark.parametrize(
    ("stage", "author", "expected"),
    [
        ("WAIT_DEADLINE", uuid4(), StageReason.WAIT_SET),
        ("IN_WORK", uuid4(), StageReason.TAKE),
        ("POOL", UUID(int=0), StageReason.WAIT_EXPIRED),
        ("POOL", uuid4(), StageReason.RESTORE),
    ],
)
def test_an_unknown_wording_is_inferred(stage, author, expected):
    event = backfill.to_history(
        legacy_row("STAGE_CHANGE", stage=stage, author_id=author, comment_text="Иное")
    )
    assert event.payload.reason is expected


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("Клиент перезакреплён", AssignmentKind.REASSIGN),
        ("Клиент взят в работу", AssignmentKind.TAKE),
    ],
)
def test_the_assignment_kind_comes_from_the_wording(comment, expected):
    event = backfill.to_history(legacy_row("OPERATOR_ASSIGN", comment_text=comment))
    assert event.payload.kind is expected
    assert event.payload.operator_id == event.actor_id
    assert event.payload.assignment_id is None
    assert event.payload.previous_operator_id is None


def test_a_past_comment_keeps_its_date():
    past = NOW - timedelta(days=40)
    event = backfill.to_history(
        legacy_row(
            "HISTORICAL_COMMENT",
            work_quarter=None,
            comment_text="Прошлый разговор",
            historical_event_date=past,
        )
    )
    assert event.occurred_at == past
    assert event.recorded_at == NOW
    assert event.work_quarter is None


def test_a_broken_legacy_record_is_reported():
    with pytest.raises(backfill.BackfillError):
        backfill.to_history(legacy_row(call_result=None))


def test_run_moves_records_and_repeats_safely(migrated):
    engine, url = migrated
    cycle = {"kcr_code": "6671-0042", "work_quarter": "2026_Q4", "cycle_id": uuid4()}
    seed(
        engine,
        [
            legacy_row(created_at=NOW),
            legacy_row(
                "HISTORICAL_COMMENT",
                work_quarter=None,
                comment_text="Прошлый",
                created_at=NOW + timedelta(seconds=1),
            ),
            legacy_row("OPERATOR_ASSIGN", created_at=NOW + timedelta(seconds=2)),
        ],
        cycle,
    )
    with forwarding(engine, url) as client:
        first = backfill.run(engine, client, batch_size=2)
        second = backfill.run(engine, client, batch_size=2)
    assert first == {"rows": 3, "accepted": 3, "duplicates": 0}
    assert second == {"rows": 3, "accepted": 0, "duplicates": 3}
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT event_type, cycle_id FROM history_events ORDER BY seq")
        ).all()
    assert [row.event_type for row in stored] == [
        "CALL_RESULT",
        "HISTORICAL_COMMENT",
        "OPERATOR_ASSIGN",
    ]
    assert stored[0].cycle_id == cycle["cycle_id"]
    assert stored[1].cycle_id is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(409, json={"code": "HISTORY_EVENT_CONFLICT"}),
        lambda request: httpx.Response(503),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
def test_a_rejected_or_unreachable_batch_stops_the_run(handler):
    with pytest.raises(backfill.BackfillError):
        backfill.post(upstream(handler), [backfill.to_history(legacy_row())], attempts=1, delay=0)


def test_a_retried_batch_is_accepted():
    responses = [httpx.Response(503), httpx.Response(200, json={"accepted": 1, "duplicates": 0})]
    result = backfill.post(
        upstream(lambda request: responses.pop(0)),
        [backfill.to_history(legacy_row())],
        attempts=2,
        delay=0,
    )
    assert result == {"accepted": 1, "duplicates": 0}


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--operator-work-database-url", "postgresql+psycopg://x"],
        [
            "--operator-work-database-url",
            "postgresql+psycopg://x",
            "--client-history-url",
            "http://history",
            "--batch-size",
            "0",
        ],
    ],
)
def test_main_requires_urls_and_a_valid_batch_size(argv, monkeypatch):
    monkeypatch.delenv("BACKFILL_OPERATOR_WORK_DATABASE_URL", raising=False)
    monkeypatch.delenv("BACKFILL_CLIENT_HISTORY_URL", raising=False)
    with pytest.raises(SystemExit) as info:
        backfill.main(argv)
    assert info.value.code == 2


def test_main_reports_counters(migrated, monkeypatch, capsys):
    engine, url = migrated
    seed(engine, [legacy_row(), legacy_row("OPERATOR_ASSIGN")])
    real_client = httpx.Client
    app = create_app(Settings(url), engine=engine, gateway=upstream(gateway))
    with TestClient(app) as app_client:
        handler = handler_for(app_client)
        monkeypatch.setattr(backfill, "create_engine", lambda *args, **kwargs: engine)
        monkeypatch.setattr(
            backfill.httpx,
            "Client",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler), base_url="http://upstream"
            ),
        )
        assert (
            backfill.main(
                [
                    "--operator-work-database-url",
                    "postgresql+psycopg://unused",
                    "--client-history-url",
                    "http://history",
                    "--batch-size",
                    "1",
                ]
            )
            == 0
        )
    assert "принято: 2" in capsys.readouterr().out
