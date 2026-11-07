"""Runs only with CLIENTS_TEST_ADMIN_URL; creates and drops its own temporary DB."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from clients_service.config import Settings
from clients_service.main import create_app

pytestmark = pytest.mark.postgres


@pytest.fixture
def database(monkeypatch):
    admin_url = os.getenv("CLIENTS_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set CLIENTS_TEST_ADMIN_URL for PostgreSQL migration tests")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_name = f"clients_test_{uuid4().hex}"
    engine = None
    created = False
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        created = True
        url = admin.url.set(database=database_name).render_as_string(hide_password=False)
        monkeypatch.setenv("CLIENTS_DATABASE_URL", url)
        engine = create_engine(url)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        yield engine, config, url
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        admin.dispose()


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
        "INSERT INTO clients(kcr_code, status) VALUES ('c', 'UNKNOWN')",
    ]:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM clients WHERE kcr_code='a'"))
        assert conn.scalar(text("SELECT count(*) FROM client_tags")) == 0
        assert conn.scalar(text("SELECT count(*) FROM clients WHERE kcr_code='b'")) == 1
