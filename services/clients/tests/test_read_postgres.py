from datetime import datetime
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from clients_service import read_rules
from clients_service.config import Settings
from clients_service.import_application import apply_import
from clients_service.import_models import ImportEnvelope
from clients_service.main import create_app
from clients_service.read_repository import get_card, get_history, list_clients

pytestmark = pytest.mark.postgres


def snapshot(code, quarter, remainder):
    return {
        "client": {"kcr_code": code},
        "snapshot": {
            "quarter": quarter,
            "total_documents": 100,
            "unsubmitted_documents": remainder,
        },
    }


@pytest.fixture
def data(database):
    engine, config, url = database
    command.upgrade(config, "head")

    def add(items, *, quarter="2026_Q1", day="2026-01-10", kind="SNAPSHOT"):
        envelope = ImportEnvelope(
            batch_id=uuid4(),
            uploaded_by=uuid4(),
            import_type=kind,
            selected_quarter=quarter,
            snapshot_date=day,
            items=items,
        )
        result = apply_import(engine, envelope)
        assert result["rejected_rows"] == 0, result
        return result

    add(
        [
            {
                "client": {
                    "kcr_code": "a",
                    "inn": "00123",
                    "tags": ["VIP", "INTEGRATOR"],
                    "raw_work_status": "private source status",
                }
            },
            {"client": {"kcr_code": "b", "inn": "00123", "tags": ["VIP"]}},
            {"client": {"kcr_code": "c", "inn": "456"}},
            {"client": {"kcr_code": "ABC%_", "inn": "789"}},
        ],
        kind="REGISTRY",
        day="2026-01-01",
    )
    return engine, url, add


def test_quarter_dynamics_uses_latest_balances_of_different_quarters(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10)])
    add([snapshot("a", "2026_Q2", 20)], quarter="2026_Q2", day="2026-04-10")
    card = get_card(engine, "a", quarter="2026_Q2")
    assert card.dynamics == "GROWING"
    assert (card.dynamics_previous_documents, card.dynamics_current_documents) == (10, 20)
    # Q2 grew between its own snapshots, yet Q2 is still below Q1.
    add([snapshot("a", "2026_Q2", 5)], quarter="2026_Q2", day="2026-04-20")
    add([snapshot("a", "2026_Q2", 7)], quarter="2026_Q2", day="2026-04-21")
    assert get_card(engine, "a", quarter="2026_Q2").dynamics == "REDUCING"
    assert get_card(engine, "a", quarter="2026_Q1").dynamics is None


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (10, 0, "ALL_SUBMITTED"),
        (0, 0, "ALL_SUBMITTED"),
        (10, 10, "NO_CHANGE"),
        (10, 2, "REDUCING"),
        (10, 12, "GROWING"),
    ],
)
def test_dynamics_all_cases_and_year_boundary(data, previous, current, expected):
    engine, _, add = data
    add([snapshot("a", "2025_Q4", previous)], quarter="2025_Q4", day="2025-12-20")
    add([snapshot("a", "2026_Q1", current)])
    card = get_card(engine, "a", quarter="2026_Q1")
    assert card.dynamics == expected
    assert card.dynamics_previous_quarter == "2025_Q4"


def test_default_calendar_quarter_missing_previous_and_sum_not_limited(data, monkeypatch):
    engine, _, add = data
    add([snapshot("a", "2026_Q2", 10)], quarter="2026_Q2", day="2026-04-10")
    add([snapshot("a", "2026_Q3", 20)], quarter="2026_Q3", day="2026-07-10")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 1, tzinfo=tz)

    monkeypatch.setattr(read_rules, "datetime", Clock)
    card = get_card(engine, "a", snapshots_limit=1)
    assert (card.dynamics_quarter, card.dynamics_previous_quarter) == ("2026_Q3", "2026_Q2")
    assert card.dynamics == "GROWING"
    assert len(card.snapshots) == 1 and card.outstanding_documents == 30

    # Default follows Moscow's calendar, not the last imported quarter.
    class NextClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, tzinfo=tz)

    monkeypatch.setattr(read_rules, "datetime", NextClock)
    assert get_card(engine, "a").dynamics is None
    assert get_card(engine, "a").outstanding_documents == 30
    assert get_card(engine, "b", quarter="2026_Q3").dynamics is None


def test_filter_before_pagination_tags_and_literal_search(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10), snapshot("b", "2026_Q1", 10)])
    add(
        [snapshot("a", "2026_Q2", 5), snapshot("b", "2026_Q2", 20)],
        quarter="2026_Q2",
        day="2026-04-10",
    )
    result = list_clients(engine, search="123", quarter="2026_Q2", dynamics="GROWING", page_size=1)
    assert result.total == 1 and result.items[0].kcr_code == "b"
    result = list_clients(engine, tags=("VIP", "INTEGRATOR"))
    assert result.total == 1 and result.items[0].kcr_code == "a"
    assert list_clients(engine, tags=("VIP", "VIP")).total == 2
    assert list_clients(engine, search="abc").items[0].kcr_code == "ABC%_"
    assert list_clients(engine, search="%_").total == 1
    assert list_clients(engine, search="' OR 1=1 --").total == 0
    result = list_clients(engine, page=10, page_size=1)
    assert result.total == 4 and result.items == []
    assert [item.kcr_code for item in list_clients(engine).items] == ["ABC%_", "a", "b", "c"]


def test_duration_first_repeat_long_reset_and_no_calendar_ageing(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10)])
    assert get_card(engine, "a", quarter="2026_Q4").duration == "FIRST_TIME"
    add([snapshot("a", "2026_Q1", 10)], day="2026-01-20")
    assert get_card(engine, "a").duration == "REPEATED"
    add([snapshot("a", "2026_Q1", 5)], quarter="2026_Q2", day="2026-04-10")
    assert get_card(engine, "a").duration == "LONG_DELAY_2Q"
    assert list_clients(engine, duration="LONG_DELAY_2Q").total == 1
    add([snapshot("a", "2026_Q1", 0)], quarter="2026_Q2", day="2026-04-20")
    assert get_card(engine, "a").duration is None
    add([snapshot("a", "2026_Q1", 3)], quarter="2026_Q3", day="2026-07-10")
    assert get_card(engine, "a").duration == "FIRST_TIME"
    add([snapshot("a", "2026_Q1", 2)], quarter="2026_Q3", day="2026-07-20")
    assert get_card(engine, "a").duration == "REPEATED"


def test_batch_row_order_cannot_create_zero_or_fake_repeat(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10), snapshot("a", "2026_Q2", 5)], quarter="2026_Q1")
    assert get_card(engine, "a").duration == "FIRST_TIME"
    # An intermediate row could make Q1 zero; the complete packet keeps Q2 positive.
    add(
        [snapshot("a", "2026_Q1", 0), snapshot("a", "2026_Q2", 7)],
        quarter="2026_Q2",
        day="2026-04-10",
    )
    assert get_card(engine, "a").duration == "LONG_DELAY_2Q"
    assert get_card(engine, "a").outstanding_documents == 7


def test_history_pages_latest_correction_first_and_quarter_scope(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10)])
    add([snapshot("a", "2026_Q1", 7)])
    add([snapshot("a", "2026_Q2", 3)], quarter="2026_Q2", day="2026-04-10")
    first = get_history(engine, "a", quarter="2026_Q1", limit=1)
    second = get_history(engine, "a", quarter="2026_Q1", limit=1, offset=1)
    assert first.total == second.total == 2
    assert first.items[0].unsubmitted_documents == 7
    assert second.items[0].unsubmitted_documents == 10
    assert first.items[0].observation_order > second.items[0].observation_order
    assert get_history(engine, "a").total == 3
    assert get_history(engine, "b").items == []


def test_http_read_opt_in_validation_and_no_private_columns(data):
    engine, url, _ = data
    with TestClient(create_app(Settings(database_url=url))) as client:
        assert client.get("/internal/v1/clients").status_code == 404
    with TestClient(create_app(Settings(database_url=url, enable_registry_read=True))) as client:
        assert client.get("/internal/v1/clients/a").status_code == 200
        assert client.get("/internal/v1/clients/missing").status_code == 404
        assert client.get("/internal/v1/clients/missing/snapshots/history").status_code == 404
        assert client.get("/internal/v1/clients/b/snapshots/history").json()["total"] == 0
        assert (
            client.get("/internal/v1/clients", params={"tags": "VIP,INTEGRATOR"}).json()["total"]
            == 1
        )
        for parameters in (
            {"quarter": "2026_Q5"},
            {"page": 0},
            {"page_size": 101},
            {"tags": "UNKNOWN"},
            {"operator_id": "someone"},
        ):
            assert client.get("/internal/v1/clients", params=parameters).status_code == 400
        assert client.get("/internal/v1/clients/a/snapshots/history?offset=-1").status_code == 400
        assert client.get("/internal/v1/clients?tags=VIP&tags=INTEGRATOR").status_code == 400
        for path in ("/internal/v1/clients", "/internal/v1/clients/a"):
            response = client.get(path)
            assert response.status_code == 200
            assert "raw_work_status" not in response.text
            assert "private source status" not in response.text
            assert "previous_state" not in response.text
            assert "assigned_operator_id" not in response.text


def test_one_request_reads_one_database_snapshot(data):
    engine, _, add = data
    add([snapshot("a", "2026_Q1", 10)])
    fired = False

    def concurrent_insert(conn, cursor, statement, parameters, context, executemany):
        nonlocal fired
        # Insert between list count and page fetch using a separate connection.
        if "SELECT count(*) FROM metrics" in statement and not fired:
            fired = True

    def after_count(conn, cursor, statement, parameters, context, executemany):
        if "SELECT count(*) FROM metrics" in statement:
            with engine.begin() as writer:
                writer.execute(text("INSERT INTO clients(kcr_code) VALUES ('new')"))

    event.listen(engine, "before_cursor_execute", concurrent_insert)
    event.listen(engine, "after_cursor_execute", after_count)
    try:
        result = list_clients(engine)
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_insert)
        event.remove(engine, "after_cursor_execute", after_count)
    assert fired
    assert result.total == len(result.items) == 4
    assert list_clients(engine).total == 5


def test_read_failure_is_sanitized_and_process_stays_live(data):
    engine, url, _ = data
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE clients RENAME TO temporarily_missing"))
    with TestClient(create_app(Settings(database_url=url, enable_registry_read=True))) as client:
        response = client.get("/internal/v1/clients")
        assert response.status_code == 503
        assert response.json() == {"code": "SERVICE_UNAVAILABLE", "message": "Try again later"}
        assert client.get("/health/live").status_code == 200
