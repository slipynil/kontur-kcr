"""Runs only with CLIENTS_TEST_ADMIN_URL; creates and drops its own temporary DB."""

import json
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from clients_service.config import Settings
from clients_service.main import create_app

pytestmark = pytest.mark.postgres


def test_migration_round_trip_and_readiness(database):
    engine, config, url = database
    with TestClient(create_app(Settings(database_url=url))) as client:
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "clients",
            "tags",
            "client_tags",
            "quarter_snapshots",
            "quarter_snapshot_history",
            "snapshot_field_provenance",
            "clients_import_operations",
            "clients_import_undo",
            "clients_data_operations",
            "clients_deleted_history",
        }
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM tags")) == 6
        command.downgrade(config, "base")
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200


def test_constraints_and_client_cascade(database):
    engine, config, _ = database
    command.upgrade(config, "head")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO clients(kcr_code, inn) VALUES ('a', '123'), ('b', '123')"))
        conn.execute(text("INSERT INTO client_tags(kcr_code, tag_id) VALUES ('a', 1)"))
    for statement in [
        "INSERT INTO clients(kcr_code) VALUES ('a')",
        "INSERT INTO client_tags(kcr_code, tag_id) VALUES ('missing', 1)",
    ]:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM clients WHERE kcr_code='a'"))
        assert conn.scalar(text("SELECT count(*) FROM client_tags")) == 0
        assert conn.scalar(text("SELECT count(*) FROM clients WHERE kcr_code='b'")) == 1


def test_blocking_migration_preserves_flags_independently_of_old_status(database):
    engine, config, _ = database
    command.upgrade(config, "0004_rollback_confirmation")
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO clients(kcr_code, status, is_blocked, inherited_fields, needs_review)
            VALUES ('a', 'INACTIVE', false, '{"status": {}}', true),
                   ('b', 'ACTIVE', true, '{"status": {}, "is_blocked": {}}', true)
        """)
        )

    command.upgrade(config, "0005_client_blocking_only")
    assert "status" not in {column["name"] for column in inspect(engine).get_columns("clients")}
    with engine.connect() as conn:
        assert conn.execute(
            text("""
            SELECT is_blocked, inherited_fields, needs_review FROM clients ORDER BY kcr_code
        """)
        ).all() == [(False, {}, False), (True, {"is_blocked": {}}, True)]


def test_registry_migration_cleans_profile_sources_and_undo(database):
    engine, config, _ = database
    command.upgrade(config, "0005_client_blocking_only")
    registry, snapshot = uuid4(), uuid4()
    metadata = {"quarter": "2026_Q2", "snapshot_date": "2026-04-20"}
    registry_source = {"batch_id": str(registry), **metadata}
    snapshot_source = {"batch_id": str(snapshot), **metadata}
    sources = {"manager_name": registry_source, "snapshots.2026_Q1.total": snapshot_source}
    prior = {"clients": [{"field_sources": sources, "inherited_fields": sources}]}
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO clients_import_operations(batch_id, payload_hash, result)
                VALUES
                    (:registry, 'r', '{"accepted_rows": 1, "created_history_records": 0}'),
                    (:snapshot, 's', '{"accepted_rows": 1, "created_history_records": 1}')
            """),
            {"registry": registry, "snapshot": snapshot},
        )
        conn.execute(
            text("""
                INSERT INTO clients(kcr_code, field_sources, inherited_fields)
                VALUES ('a', CAST(:sources AS jsonb), CAST(:sources AS jsonb))
            """),
            {"sources": json.dumps(sources)},
        )
        conn.execute(
            text("""
                INSERT INTO clients_import_undo(batch_id, kcr_code, previous_state)
                VALUES (:snapshot, 'a', CAST(:state AS jsonb))
            """),
            {"snapshot": snapshot, "state": json.dumps(prior)},
        )
    command.upgrade(config, "head")
    cleaned = {**sources, "manager_name": {"batch_id": str(registry)}}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT field_sources, inherited_fields FROM clients")).one() == (
            cleaned,
            cleaned,
        )
        assert conn.scalar(text("SELECT previous_state FROM clients_import_undo")) == {
            "clients": [{"field_sources": cleaned, "inherited_fields": cleaned}]
        }
