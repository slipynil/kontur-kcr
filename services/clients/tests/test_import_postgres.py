from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from clients_service.config import Settings
from clients_service.import_application import PayloadConflict, apply_import
from clients_service.import_models import ImportEnvelope
from clients_service.main import create_app

pytestmark = pytest.mark.postgres


def packet(items, *, kind="SNAPSHOT", day="2026-04-20", quarter="2026_Q2", batch_id=None):
    return ImportEnvelope(
        batch_id=batch_id or uuid4(),
        uploaded_by="00000000-0000-0000-0000-000000000001",
        import_type=kind,
        selected_quarter=quarter,
        snapshot_date=day,
        items=items,
    )


def row(code="a", *, quarter="2026_Q1", total=100, remainder=10):
    return {
        "client": {"kcr_code": code},
        "snapshot": {
            "quarter": quarter,
            "total_documents": total,
            "unsubmitted_documents": remainder,
        },
    }


@pytest.fixture
def ready_database(database):
    engine, config, url = database
    command.upgrade(config, "head")
    return engine, url


def register(engine):
    return apply_import(
        engine,
        packet(
            [
                {
                    "client": {
                        "kcr_code": "a",
                        "inn": "00123",
                        "manager_name": "manager",
                        "tags": ["VIP"],
                    }
                },
                {"client": {"kcr_code": "b", "inn": "00123"}},
            ],
            kind="REGISTRY",
            day="2026-04-01",
        ),
    )


def current(engine, quarter="2026_Q1"):
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text("SELECT * FROM quarter_snapshots WHERE kcr_code='a' AND quarter=:quarter"),
                {"quarter": quarter},
            )
            .mappings()
            .one()
        )


def test_apply_idempotency_conflict_and_atomic_undo(ready_database):
    engine, _ = ready_database
    register(engine)
    envelope = packet([row()])
    result = apply_import(engine, envelope)
    assert result["accepted_rows"] == result["created_history_records"] == 1
    assert result == apply_import(engine, envelope)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshot_history")) == 1
        undo = (
            conn.execute(
                text(
                    "SELECT previous_state, created_history_ids "
                    "FROM clients_import_undo WHERE batch_id=:id"
                ),
                {"id": envelope.batch_id},
            )
            .mappings()
            .one()
        )
        assert undo["previous_state"]["quarter_snapshots"] == []
        assert len(undo["created_history_ids"]) == 1
    changed = envelope.model_copy(update={"items": [row(remainder=5)]})
    with pytest.raises(PayloadConflict):
        apply_import(engine, changed)
    assert current(engine)["unsubmitted_documents"] == 10


def test_partial_errors_savepoint_and_unknown_client(ready_database):
    engine, _ = ready_database
    register(engine)
    # Failed snapshot must not leave the attempted profile update behind.
    bad = {
        "client": {"kcr_code": "a", "manager_name": "must_not_persist"},
        "snapshot": {"quarter": "2026_Q1", "total_documents": 100},
    }
    result = apply_import(
        engine,
        packet(
            [
                bad,
                row("missing"),
                row("b", remainder=2),
                row("b"),
                row("a", remainder=-1),
            ]
        ),
    )
    assert (result["accepted_rows"], result["rejected_rows"]) == (1, 4)
    assert [error["row_number"] for error in result["errors"]] == [1, 2, 4, 5]
    assert "must_not_persist" not in str(result)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT manager_name FROM clients WHERE kcr_code='a'")) == "manager"
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshots WHERE kcr_code='a'")) == 0


def test_same_date_corrections_history_and_old_date(ready_database):
    engine, _ = ready_database
    register(engine)
    apply_import(engine, packet([row()]))
    apply_import(engine, packet([row(total=500, remainder=7)]))
    assert current(engine)["unsubmitted_documents"] == 7
    assert current(engine)["total_documents"] == 100
    apply_import(engine, packet([row(total=999, remainder=40)], day="2026-04-10"))
    assert current(engine)["unsubmitted_documents"] == 7
    # Same count, later date is still a new observation.
    apply_import(engine, packet([row(remainder=7)], day="2026-04-21"))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshot_history")) == 4
        assert (
            conn.scalar(
                text("SELECT count(DISTINCT observation_order) FROM quarter_snapshot_history")
            )
            == 4
        )


def test_snapshot_inheritance_clear_and_cross_quarter_isolation(ready_database):
    engine, _ = ready_database
    register(engine)
    original = packet([row()], day="2026-04-10")
    apply_import(engine, original)
    inherited = packet([row(total=None, remainder=None)], day="2026-04-20")
    apply_import(engine, inherited)
    assert current(engine)["unsubmitted_documents"] == 10
    with engine.connect() as conn:
        client = conn.execute(
            text("SELECT needs_review, inherited_fields FROM clients WHERE kcr_code='a'")
        )
        needs_review, fields = client.one()
        assert needs_review
        assert fields["snapshots.2026_Q1.unsubmitted_documents"]["batch_id"] == str(
            original.batch_id
        )
    # Q2 cannot inherit Q1's counts; the retained Q1 still exists once.
    rejected = apply_import(engine, packet([row(quarter="2026_Q2", total=None, remainder=None)]))
    assert rejected["rejected_rows"] == 1
    apply_import(engine, packet([row(total=None, remainder=0)], day="2026-04-21"))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT needs_review FROM clients WHERE kcr_code='a'")) is False
        assert conn.scalar(text("SELECT count(*) FROM snapshot_field_provenance")) == 0
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshots WHERE kcr_code='a'")) == 1


def test_historical_blank_does_not_borrow_future_or_confirm_warning(ready_database):
    engine, _ = ready_database
    register(engine)
    apply_import(engine, packet([row()], day="2026-04-20"))
    rejected = apply_import(engine, packet([row(remainder=None)], day="2026-04-10"))
    assert rejected["rejected_rows"] == 1
    apply_import(engine, packet([row(remainder=None)], day="2026-04-21"))
    apply_import(engine, packet([row(remainder=8)], day="2026-04-19"))
    assert current(engine)["unsubmitted_documents"] == 10
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT needs_review FROM clients WHERE kcr_code='a'")) is True


def test_profile_warning_tags_false_and_older_values(ready_database):
    engine, _ = ready_database
    register(engine)
    apply_import(
        engine, packet([{"client": {"kcr_code": "a", "manager_name": None}}], kind="REGISTRY")
    )
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT needs_review FROM clients WHERE kcr_code='a'")) is True
    apply_import(
        engine,
        packet(
            [
                {
                    "client": {
                        "kcr_code": "a",
                        "manager_name": "confirmed",
                        "tags": [],
                        "is_blocked": False,
                    }
                }
            ],
            kind="REGISTRY",
            day="2026-04-22",
        ),
    )
    apply_import(
        engine,
        packet(
            [{"client": {"kcr_code": "a", "manager_name": "historical"}}],
            kind="REGISTRY",
            day="2026-04-01",
        ),
    )
    with engine.connect() as conn:
        client = conn.execute(text("SELECT * FROM clients WHERE kcr_code='a'")).mappings().one()
        assert client["manager_name"] == "confirmed"
        assert not client["needs_review"] and not client["is_blocked"]
        assert client["inn"] == "00123"
        assert conn.scalar(text("SELECT count(*) FROM client_tags WHERE kcr_code='a'")) == 0


def test_database_encoding_error_does_not_abort_neighbours(ready_database):
    engine, _ = ready_database
    result = apply_import(
        engine,
        packet(
            [
                {"client": {"kcr_code": "bad", "organization_name": "bad\x00text"}},
                {"client": {"kcr_code": "good", "organization_name": "accepted"}},
            ],
            kind="REGISTRY",
        ),
    )
    assert (result["accepted_rows"], result["rejected_rows"]) == (1, 1)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM clients")) == 1
        assert conn.scalar(text("SELECT kcr_code FROM clients")) == "good"


def test_concurrent_repeat_commits_once(ready_database):
    engine, _ = ready_database
    register(engine)
    envelope = packet([row()])
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply_import(engine, envelope), range(2)))
    assert results[0] == results[1]
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshot_history")) == 1


def test_two_periods_one_client_and_atomic_failure(ready_database):
    engine, _ = ready_database
    register(engine)
    envelope = packet([row(), row(quarter="2026_Q2", remainder=4)])

    def fail_result_commit(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO clients_import_operations"):
            raise RuntimeError("simulated failure before commit")

    event.listen(engine, "before_cursor_execute", fail_result_commit)
    try:
        with pytest.raises(RuntimeError):
            apply_import(engine, envelope)
    finally:
        event.remove(engine, "before_cursor_execute", fail_result_commit)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshots")) == 0
        assert conn.scalar(text("SELECT count(*) FROM quarter_snapshot_history")) == 0
        assert conn.scalar(text("SELECT count(*) FROM clients_import_operations")) == 1
    result = apply_import(engine, envelope)
    assert result["updated_clients"] == 1
    assert result["created_snapshots"] == 2
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT sum(unsubmitted_documents) FROM quarter_snapshots")) == 14
        undo = (
            conn.execute(
                text(
                    "SELECT previous_state, created_history_ids "
                    "FROM clients_import_undo WHERE batch_id=:id"
                ),
                {"id": envelope.batch_id},
            )
            .mappings()
            .one()
        )
        assert len(undo["created_history_ids"]) == 2
        assert undo["previous_state"]["quarter_snapshots"] == []


def test_http_disabled_by_default_and_local_opt_in(ready_database):
    engine, url = ready_database
    with TestClient(create_app(Settings(database_url=url))) as client:
        assert client.post("/internal/v1/import/apply", json={}).status_code == 404
        assert "/internal/v1/import/apply" not in client.get("/openapi.json").json()["paths"]
    with TestClient(create_app(Settings(database_url=url, enable_import_apply=True))) as client:
        payload = packet([{"client": {"kcr_code": "a"}}], kind="REGISTRY").model_dump(mode="json")
        assert client.post("/internal/v1/import/apply", json=payload).status_code == 200
        payload["items"] = [{"client": {"kcr_code": "b"}}]
        assert client.post("/internal/v1/import/apply", json=payload).status_code == 409
        invalid = client.post("/internal/v1/import/apply", json={"private": "sensitive"})
        assert invalid.status_code == 400 and "sensitive" not in invalid.text
