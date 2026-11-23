import os
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from conftest import NOW, SERVICE_ROOT, batch, sample, upstream
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from client_history.api import create_app
from client_history.config import Settings
from client_history.database import HistoryCleanupRow, HistoryEventRow
from client_history.errors import HistoryError
from client_history.models import HistoryCleanupRequest
from client_history.service import History
from client_history.upstreams import Names, Registry

SCHEMA_QUERIES = [
    """
    SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
           a.attidentity, COALESCE(pg_get_expr(d.adbin, d.adrelid), '')
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef AS d ON d.adrelid = c.oid AND d.adnum = a.attnum
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
    ORDER BY 1, 2
    """,
    """
    SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
    FROM pg_constraint AS con
    JOIN pg_class AS c ON c.oid = con.conrelid
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname <> 'alembic_version'
    ORDER BY 1, 2
    """,
    """
    SELECT tablename, indexname, indexdef FROM pg_indexes
    WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    ORDER BY 1, 2
    """,
    """
    SELECT c.relname, COALESCE(a.attname, ''),
           CASE WHEN a.attnum IS NULL THEN obj_description(c.oid, 'pg_class')
                ELSE col_description(c.oid, a.attnum) END
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    LEFT JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
    ORDER BY 1, 2
    """,
]


def snapshot(engine) -> list:
    with engine.connect() as connection:
        return [tuple(row) for query in SCHEMA_QUERIES for row in connection.execute(text(query))]


def test_migration_round_trip_and_readiness(database):
    engine, config, url = database
    with TestClient(create_app(Settings(url))) as client:
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "history_events",
            "history_cleanup_operations",
        }
        command.downgrade(config, "base")
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200


def test_migration_matches_the_approved_ddl(database):
    engine, config, _ = database
    command.upgrade(config, "head")
    ddl_path = SERVICE_ROOT.joinpath("../../docs/db/ddl/client_history.sql").resolve()
    if not ddl_path.exists():
        pytest.skip("Утверждённый DDL есть только в полном дереве репозитория")
    admin = create_engine(os.environ["CLIENT_HISTORY_TEST_ADMIN_URL"], isolation_level="AUTOCOMMIT")
    name = f"client_history_docs_{uuid4().hex}"
    docs = None
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        url = admin.url.set(database=name).render_as_string(hide_password=False)
        docs = create_engine(url)
        ddl = ddl_path.read_text(encoding="utf-8")
        with docs.begin() as connection:
            connection.exec_driver_sql(ddl)
        assert snapshot(docs) == snapshot(engine)
    finally:
        if docs is not None:
            docs.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_ingest_saves_new_records_and_skips_repeats(history):
    first, second = sample(), sample("PUBLICATION")
    result = history.ingest(batch(first, second))
    assert (result.accepted, result.duplicates) == (2, 0)
    result = history.ingest(batch(first, second, sample()))
    assert (result.accepted, result.duplicates) == (1, 2)


def test_ingest_conflict_keeps_the_batch_atomic(history):
    stored = sample()
    history.ingest(batch(stored))
    changed = dict(stored, payload={"comment_text": "Другой текст"})
    other = sample()
    with pytest.raises(HistoryError) as info:
        history.ingest(batch(changed, other))
    assert (info.value.status, info.value.code) == (409, "HISTORY_EVENT_CONFLICT")
    page = history.list("6671-0042", None, 1, 20)
    assert page.total == 1


def test_ingest_rejects_a_repeated_id_inside_one_batch(history):
    event = sample()
    with pytest.raises(HistoryError) as info:
        history.ingest(batch(event, event))
    assert info.value.status == 422
    assert history.list("6671-0042", None, 1, 20).total == 0


def test_ingest_retries_a_concurrent_write(migrated, monkeypatch):
    engine, _ = migrated
    history = History(engine, Names(None), Registry(None))
    original = History._insert
    calls = []

    def flaky(self, source, pending):
        calls.append(len(pending))
        if len(calls) == 1:
            return False
        return original(self, source, pending)

    monkeypatch.setattr(History, "_insert", flaky)
    assert history.ingest(batch(sample())).accepted == 1
    assert calls == [1, 1]


def test_ingest_gives_up_after_the_attempts(migrated, monkeypatch):
    engine, _ = migrated
    history = History(engine, Names(None), Registry(None), attempts=2)
    monkeypatch.setattr(History, "_insert", lambda self, source, pending: False)
    with pytest.raises(HistoryError) as info:
        history.ingest(batch(sample()))
    assert (info.value.status, info.value.code) == (503, "HISTORY_UNAVAILABLE")


def test_names_are_resolved_once_and_cached(migrated):
    engine, _ = migrated
    actor = uuid4()
    calls = []

    def handler(request):
        calls.append(request.url.params["ids"])
        user = {"id": str(actor), "login": "operator", "display_name": "operator"}
        return httpx.Response(200, json={"items": [user]})

    history = History(engine, Names(upstream(handler)), Registry(None))
    history.ingest(batch(sample(actor_id=str(actor))))
    history.ingest(batch(sample(actor_id=str(actor))))
    assert calls == [str(actor)]
    with engine.connect() as connection:
        assert set(connection.scalars(select(HistoryEventRow.actor_name))) == {"operator"}


def test_a_batch_with_an_unnamed_author_needs_the_gateway(history):
    with pytest.raises(HistoryError) as info:
        history.ingest(batch(sample(actor_id=str(uuid4()))))
    assert (info.value.status, info.value.code) == (503, "HISTORY_UNAVAILABLE")
    assert history.list("6671-0042", None, 1, 20).total == 0


def test_a_named_author_does_not_need_the_gateway(history):
    assert history.ingest(batch(sample(actor_id=str(uuid4()), actor_name="operator"))).accepted == 1


def test_ordering_follows_the_event_date_then_the_sequence(history):
    old = sample("HISTORICAL_COMMENT", occurred_at=(NOW - timedelta(days=2)).isoformat())
    middle = sample("CALL_RESULT", occurred_at=(NOW - timedelta(hours=1)).isoformat())
    latest_first = sample("COMMENT", id=str(uuid4()))
    latest_second = sample("PUBLICATION")
    history.ingest(batch(old, middle, latest_first, latest_second))
    page = history.list("6671-0042", None, 1, 20)
    assert [str(item.id) for item in page.items] == [
        latest_second["id"],
        latest_first["id"],
        middle["id"],
        old["id"],
    ]


def test_pagination_and_cycle_filter(history):
    events = [sample() for _ in range(3)]
    events.append(sample(work_quarter="2026_Q3"))
    history.ingest(batch(*events))
    page = history.list("6671-0042", None, 2, 2)
    assert (page.total, page.page, page.page_size, len(page.items)) == (4, 2, 2, 2)
    assert history.list("6671-0042", "2026_Q3", 1, 20).total == 1
    assert history.list("6671-0042", None, 5, 20).items == []


def test_an_empty_chronicle_asks_the_registry(migrated):
    engine, _ = migrated
    statuses = [200, 404, 500]

    def handler(request):
        assert request.url.path == "/internal/v1/clients/6671-0042"
        return httpx.Response(statuses.pop(0))

    history = History(engine, Names(None), Registry(upstream(handler)))
    assert history.list("6671-0042", None, 1, 20).total == 0
    with pytest.raises(HistoryError) as info:
        history.list("6671-0042", None, 1, 20)
    assert (info.value.status, info.value.code) == (404, "CLIENT_NOT_FOUND")
    with pytest.raises(HistoryError) as info:
        history.list("6671-0042", None, 1, 20)
    assert info.value.status == 503


def test_a_stored_record_needs_no_registry_check(migrated):
    engine, _ = migrated

    def handler(request):
        raise AssertionError("the registry must not be asked")

    history = History(engine, Names(None), Registry(upstream(handler)))
    history.ingest(batch(sample()))
    assert history.list("6671-0042", None, 1, 20).total == 1


def cleanup_command(scope="CLIENT", **overrides) -> HistoryCleanupRequest:
    body = {"operation_id": str(uuid4()), "kcr_code": "6671-0042", "scope": scope, **overrides}
    return HistoryCleanupRequest.model_validate(body)


def test_client_cleanup_removes_every_record_of_the_client(history):
    history.ingest(batch(sample(), sample("PUBLICATION"), sample(kcr_code="other")))
    result = history.cleanup(cleanup_command())
    assert (result.deleted, result.scope) == (2, "CLIENT")
    assert history.list("6671-0042", None, 1, 20).total == 0
    assert history.list("other", None, 1, 20).total == 1


def test_targeted_cleanup_removes_listed_records_and_cycles(history):
    cycle = str(uuid4())
    listed = sample()
    of_cycle = sample("STAGE_CHANGE", cycle_id=cycle)
    kept = sample()
    other_client = sample(kcr_code="other", cycle_id=cycle)
    history.ingest(batch(listed, of_cycle, kept, other_client))
    result = history.cleanup(
        cleanup_command("TARGETED", event_ids=[listed["id"]], cycle_ids=[cycle])
    )
    assert result.deleted == 2
    assert history.list("6671-0042", None, 1, 20).total == 1
    assert history.list("other", None, 1, 20).total == 1


def test_cleanup_replay_keeps_records_created_after_it(history):
    history.ingest(batch(sample()))
    operation = cleanup_command()
    assert history.cleanup(operation).deleted == 1
    history.ingest(batch(sample()))
    replay = history.cleanup(operation)
    assert replay.deleted == 1
    assert history.list("6671-0042", None, 1, 20).total == 1


def test_cleanup_rejects_a_reused_operation_id(history):
    operation = cleanup_command()
    history.cleanup(operation)
    changed = HistoryCleanupRequest.model_validate(
        operation.model_dump(mode="json") | {"scope": "TARGETED", "cycle_ids": [str(uuid4())]}
    )
    with pytest.raises(HistoryError) as info:
        history.cleanup(changed)
    assert (info.value.status, info.value.code) == (409, "OPERATION_ID_CONFLICT")


def test_a_failed_cleanup_records_nothing(history, monkeypatch):
    history.ingest(batch(sample()))

    def broken(connection, command):
        raise SQLAlchemyError("private details")

    monkeypatch.setattr(History, "_delete", staticmethod(broken))
    with pytest.raises(SQLAlchemyError):
        history.cleanup(cleanup_command())
    assert history.list("6671-0042", None, 1, 20).total == 1
    with history.engine.connect() as connection:
        assert connection.scalar(select(HistoryCleanupRow.operation_id)) is None


def test_read_keeps_the_compatibility_fields(history):
    actor = uuid4()
    call = sample(
        "CALL_RESULT",
        actor_id=str(actor),
        actor_name="operator",
        payload={
            "call_result": "REFUSAL",
            "comment_text": "Отказ",
            "promised_date": "2026-12-01",
            "wait_until": "2026-12-05",
        },
    )
    past = sample("HISTORICAL_COMMENT")
    assign = sample("OPERATOR_ASSIGN", payload={"operator_id": str(actor), "kind": "REASSIGN"})
    publication = sample("PUBLICATION")
    stage = sample("STAGE_CHANGE", payload={"to_stage": "POOL", "reason": "WAIT_EXPIRED"})
    history.ingest(batch(call, past, assign, publication, stage))
    items = {item.event_type: item for item in history.list("6671-0042", None, 1, 20).items}

    stored_call = items["CALL_RESULT"]
    assert stored_call.author_id == actor
    assert stored_call.actor_name == "operator"
    assert stored_call.comment_text == "Отказ"
    assert stored_call.call_result == "REFUSAL"
    assert str(stored_call.promised_date) == "2026-12-01"
    assert str(stored_call.wait_until) == "2026-12-05"
    assert stored_call.stage == "WAIT_DEADLINE"
    assert stored_call.historical_event_date is None
    assert stored_call.created_at == stored_call.recorded_at
    assert stored_call.source == "OPERATOR_WORK"
    assert stored_call.seq > 0

    stored_past = items["HISTORICAL_COMMENT"]
    assert stored_past.historical_event_date == stored_past.occurred_at
    assert stored_past.work_quarter is None

    assert items["OPERATOR_ASSIGN"].comment_text == "Клиент перезакреплён"
    assert items["PUBLICATION"].comment_text == "Клиент передан в обзвон"
    assert items["PUBLICATION"].payload == {}

    stored_stage = items["STAGE_CHANGE"]
    assert stored_stage.comment_text == "Срок ожидания истёк. Клиент возвращён в пул"
    assert stored_stage.stage == "POOL"
    assert stored_stage.actor_id is None
    assert stored_stage.author_id == UUID(int=0)
