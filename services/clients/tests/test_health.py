from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from clients_service.config import Settings
from clients_service.database import SCHEMA_REVISION
from clients_service.main import create_app


class FakeEngine:
    def __init__(self, revisions=None, unavailable=False):
        self.revisions = revisions or []
        self.unavailable = unavailable
        self.disposed = False

    @contextmanager
    def connect(self):
        if self.unavailable:
            raise OperationalError("private query", {}, Exception("private password"))
        yield self

    def execute(self, statement):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.revisions

    def dispose(self):
        self.disposed = True


@pytest.mark.parametrize(
    ("revisions", "unavailable", "expected"),
    [
        ([SCHEMA_REVISION], False, 200),
        ([], False, 503),
        (["old"], False, 503),
        ([SCHEMA_REVISION, "unexpected_branch"], False, 503),
        ([], True, 503),
    ],
)
def test_health_and_engine_cleanup(monkeypatch, revisions, unavailable, expected):
    engine = FakeEngine(revisions, unavailable)
    monkeypatch.setattr("clients_service.main.build_engine", lambda settings: engine)
    settings = Settings(database_url="postgresql+psycopg://user:password@localhost/clients")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/health/ready")
        assert response.status_code == expected
        assert response.json() == {"status": "ok" if expected == 200 else "not_ready"}
        assert "password" not in response.text
    assert engine.disposed


def test_missing_schema_is_not_ready(monkeypatch):
    engine = FakeEngine()

    def missing_table(statement):
        raise OperationalError("query", {}, Exception("relation does not exist"))

    engine.execute = missing_table
    monkeypatch.setattr("clients_service.main.build_engine", lambda settings: engine)
    with TestClient(create_app(Settings(database_url="postgresql+psycopg://localhost/test"))) as c:
        assert c.get("/health/ready").status_code == 503


def test_configuration_does_not_expose_password():
    settings = Settings(database_url="postgresql+psycopg://user:private_password@localhost/db")
    assert "private_password" not in repr(settings)


@pytest.mark.parametrize("url", ["sqlite:///db", "postgresql://localhost/db", "nonsense"])
def test_reject_unsupported_database_url(url):
    with pytest.raises(ValueError):
        Settings(database_url=url)
