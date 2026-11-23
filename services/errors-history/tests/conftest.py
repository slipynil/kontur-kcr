import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from errors_history.api import create_app

SERVICE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(monkeypatch):
    admin_url = os.getenv("ERRORS_HISTORY_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set ERRORS_HISTORY_TEST_ADMIN_URL for PostgreSQL tests")

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = f"errors_history_test_{uuid4().hex}"
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        url = admin.url.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("ERRORS_HISTORY_DATABASE_URL", url)
        try:
            yield Config(str(SERVICE_ROOT / "alembic.ini"))
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        admin.dispose()


@pytest.fixture
def migrated(database):
    command.upgrade(database, "head")
    return database


@pytest.fixture
def client(migrated):
    with TestClient(create_app()) as http:
        yield http
