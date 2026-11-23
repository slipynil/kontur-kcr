import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from client_history.models import HistoryEventBatch
from client_history.service import History
from client_history.upstreams import Names, Registry

SERVICE_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
PAYLOADS = {
    "CALL_RESULT": {"call_result": "AGREEMENT", "comment_text": "Договорились"},
    "HISTORICAL_COMMENT": {"comment_text": "Прошлый разговор"},
    "COMMENT": {"comment_text": "Комментарий"},
    "OPERATOR_ASSIGN": {"operator_id": str(uuid4()), "kind": "TAKE"},
    "PUBLICATION": {},
    "STAGE_CHANGE": {"to_stage": "POOL", "reason": "SNAPSHOT"},
    "EMAIL_SENT": {},
    "CLAIM_UPDATE": {},
}


@pytest.fixture
def database(monkeypatch):
    """Creates and drops its own temporary database; needs CREATE DATABASE rights."""
    admin_url = os.getenv("CLIENT_HISTORY_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set CLIENT_HISTORY_TEST_ADMIN_URL for PostgreSQL tests")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"client_history_test_{uuid4().hex}"
    engine = None
    created = False
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        url = admin.url.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("CLIENT_HISTORY_DATABASE_URL", url)
        engine = create_engine(url)
        yield engine, Config(str(SERVICE_ROOT / "alembic.ini")), url
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def migrated(database):
    engine, config, url = database
    command.upgrade(config, "head")
    return engine, url


@pytest.fixture
def history(migrated):
    engine, _ = migrated
    return History(engine, Names(None), Registry(None))


def sample(event_type: str = "COMMENT", **overrides) -> dict:
    event = {
        "id": str(uuid4()),
        "kcr_code": "6671-0042",
        "event_type": event_type,
        "work_quarter": "2026_Q4",
        "occurred_at": NOW.isoformat(),
        "recorded_at": NOW.isoformat(),
        "payload": dict(PAYLOADS[event_type]),
    }
    if event_type == "HISTORICAL_COMMENT":
        event["work_quarter"] = None
        event["occurred_at"] = (NOW - timedelta(days=30)).isoformat()
    event.update(overrides)
    return event


def batch(*events, source: str = "OPERATOR_WORK") -> HistoryEventBatch:
    return HistoryEventBatch.model_validate({"source": source, "events": list(events)})


def upstream(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://upstream", trust_env=False
    )
