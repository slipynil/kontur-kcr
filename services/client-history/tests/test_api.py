from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from conftest import sample, upstream
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from client_history.api import create_app
from client_history.config import Settings


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

    def scalars(self, statement):
        return self

    def all(self):
        return self.revisions

    def dispose(self):
        self.disposed = True


@pytest.fixture
def client(migrated):
    engine, url = migrated
    with TestClient(create_app(Settings(url), engine=engine)) as test_client:
        yield test_client


def post(client, *events):
    return client.post(
        "/internal/v1/history/events", json={"source": "OPERATOR_WORK", "events": list(events)}
    )


def test_ingest_and_repeat(client):
    event = sample()
    assert post(client, event).json() == {"accepted": 1, "duplicates": 0}
    assert post(client, event).json() == {"accepted": 0, "duplicates": 1}


def test_ingest_conflict(client):
    event = sample()
    post(client, event)
    response = post(client, dict(event, payload={"comment_text": "Другой текст"}))
    assert response.status_code == 409
    assert response.json()["code"] == "HISTORY_EVENT_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [
        {"source": "OPERATOR_WORK", "events": []},
        {"source": "CLIENTS", "events": [sample()]},
        {"source": "OPERATOR_WORK", "events": [dict(sample(), event_type="NOTE")]},
        {"source": "OPERATOR_WORK", "events": [sample(work_quarter="2026_Q5")]},
        {"events": [sample()]},
    ],
)
def test_invalid_ingest_body(client, body):
    response = client.post("/internal/v1/history/events", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_cleanup_and_reused_operation_id(client):
    post(client, sample())
    command = {"operation_id": str(uuid4()), "kcr_code": "6671-0042", "scope": "CLIENT"}
    response = client.post("/internal/v1/history/cleanup", json=command)
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert client.post("/internal/v1/history/cleanup", json=command).json()["deleted"] == 1
    changed = command | {"scope": "TARGETED", "cycle_ids": [str(uuid4())]}
    response = client.post("/internal/v1/history/cleanup", json=changed)
    assert response.status_code == 409
    assert response.json()["code"] == "OPERATION_ID_CONFLICT"


def test_cleanup_without_targets(client):
    response = client.post(
        "/internal/v1/history/cleanup",
        json={"operation_id": str(uuid4()), "kcr_code": "6671-0042", "scope": "TARGETED"},
    )
    assert response.status_code == 422


def test_list_returns_the_chronicle(client):
    post(client, sample("HISTORICAL_COMMENT"), sample("PUBLICATION"))
    response = client.get("/internal/v1/clients/6671-0042/events")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["event_type"] for item in body["items"]] == ["PUBLICATION", "HISTORICAL_COMMENT"]
    assert body["items"][0]["payload"] == {}


@pytest.mark.parametrize(
    "query",
    ["page=0", "page_size=0", "page_size=101", "work_quarter=2026_Q5"],
)
def test_invalid_list_query(client, query):
    response = client.get(f"/internal/v1/clients/6671-0042/events?{query}")
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_an_unknown_code_is_not_found(migrated):
    engine, url = migrated
    registry = upstream(lambda request: httpx.Response(404))
    with TestClient(create_app(Settings(url), engine=engine, registry=registry)) as client:
        response = client.get("/internal/v1/clients/absent/events")
    assert response.status_code == 404
    assert response.json()["code"] == "CLIENT_NOT_FOUND"


def test_an_unavailable_registry_fails_the_empty_read(migrated):
    engine, url = migrated
    registry = upstream(lambda request: httpx.Response(503))
    with TestClient(create_app(Settings(url), engine=engine, registry=registry)) as client:
        response = client.get("/internal/v1/clients/6671-0042/events")
    assert response.status_code == 503
    assert response.json()["code"] == "HISTORY_UNAVAILABLE"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/internal/v1/history/events", {"source": "OPERATOR_WORK", "events": [sample()]}),
        (
            "POST",
            "/internal/v1/history/cleanup",
            {"operation_id": str(uuid4()), "kcr_code": "1", "scope": "CLIENT"},
        ),
        ("GET", "/internal/v1/clients/1/events", None),
    ],
)
def test_an_unavailable_database_answers_503(method, path, body):
    settings = Settings("postgresql+psycopg://user:private_password@127.0.0.1:1/none")
    with TestClient(create_app(settings)) as client:
        response = client.request(method, path, json=body)
        readiness = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "HISTORY_UNAVAILABLE"
    assert readiness.status_code == 503
    assert readiness.json() == {"status": "not_ready"}
    assert "private_password" not in response.text + readiness.text


def test_health_with_a_stub_engine():
    engine = FakeEngine(["0001_client_history"])
    settings = Settings("postgresql+psycopg://user:private_password@localhost/db")
    with TestClient(create_app(settings, engine=engine)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    assert not engine.disposed


@pytest.mark.parametrize("revisions", [[], ["older"], ["0001_client_history", "branch"]])
def test_health_requires_the_expected_revision(revisions):
    with TestClient(
        create_app(Settings("postgresql+psycopg://localhost/db"), engine=FakeEngine(revisions))
    ) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_unknown_route_and_method(client):
    assert client.get("/internal/v1/unknown").json()["code"] == "NOT_FOUND"
    response = client.get("/internal/v1/history/events")
    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"


def test_openapi_keeps_the_contract_operation_ids(client):
    operations = {
        operation.get("operationId")
        for path in client.get("/openapi.json").json()["paths"].values()
        for operation in path.values()
    }
    assert {"ingestHistoryEvents", "cleanupHistory", "listHistoryEvents"} <= operations


def test_settings_do_not_expose_the_password():
    settings = Settings("postgresql+psycopg://user:private_password@localhost/db")
    assert "private_password" not in repr(settings)
