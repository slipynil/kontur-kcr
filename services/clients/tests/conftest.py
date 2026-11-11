import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine


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
