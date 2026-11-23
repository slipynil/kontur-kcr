from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient

from errors_history.api import create_app

BULK = "/internal/v1/import-errors/bulk"
CLIENT = "/internal/v1/clients/{}/import-errors"
BATCH = "/internal/v1/imports/{}/errors"
BATCH_ID = str(uuid4())
NOW = "2026-11-23T12:00:00Z"


def error(source_id, **changes):
    return {
        "source_error_id": source_id,
        "batch_id": BATCH_ID,
        "import_type": "REGISTRY",
        "kcr_code": "000123",
        "sheet": " Реестр ",
        "row_number": 12,
        "reason": "A12 — дубликат кода КЦР",
        "recorded_at": NOW,
        **changes,
    }


def post(client, *errors):
    return client.post(BULK, json={"errors": errors})


def test_reads_by_client_and_batch(client):
    other_batch_id = str(uuid4())
    workbook = error(6, kcr_code=None, sheet="", row_number=0, reason="Ошибка книги")
    errors = [
        error(1),
        error(2, batch_id=other_batch_id, import_type="SNAPSHOT", sheet="4 кв 2026"),
        error(3, recorded_at="2026-11-23T13:00:00Z"),
        error(4, recorded_at=None),
        error(5, recorded_at=None),
        workbook,
        error(7, kcr_code="123"),
    ]
    response = post(client, *errors)
    assert response.status_code == 200
    assert response.json() == {"inserted": 7, "existing": 0}

    path = CLIENT.format("000123")
    first = client.get(path, params={"page_size": 3}).json()
    assert [item["source_error_id"] for item in first["items"]] == [3, 2, 1]
    assert first["has_more"] is True
    assert first["page"] == 1
    assert first["page_size"] == 3
    second = client.get(path, params={"page_size": 3, "page": 2}).json()
    assert [item["source_error_id"] for item in second["items"]] == [5, 4]
    assert second["items"][0]["recorded_at"] is None
    assert second["has_more"] is False
    assert first["items"][-1]["sheet"] == " Реестр "
    assert first["items"][-1]["reason"] == errors[0]["reason"]

    filtered = client.get(path, params={"import_type": "SNAPSHOT"}).json()
    assert [item["source_error_id"] for item in filtered["items"]] == [2]
    assert filtered["items"][0]["kcr_code"] == "000123"
    assert len(client.get(CLIENT.format("123")).json()["items"]) == 1

    batch = client.get(BATCH.format(BATCH_ID)).json()
    assert [item["source_error_id"] for item in batch["items"]] == [3, 7, 6, 1, 5, 4]
    assert {k: v for k, v in batch["items"][2].items() if k != "id"} == workbook


@pytest.mark.parametrize("path", [CLIENT.format("unknown"), BATCH.format(uuid4())])
def test_empty_history(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "has_more": False}


def test_idempotent_replay(client):
    original = error(1)
    assert post(client, original, original).json() == {"inserted": 1, "existing": 0}
    before = client.get(BATCH.format(BATCH_ID)).json()
    same_instant = error(1, recorded_at="2026-11-23T15:00:00+03:00")
    assert post(client, same_instant).json() == {"inserted": 0, "existing": 1}
    assert client.get(BATCH.format(BATCH_ID)).json() == before


def test_conflict_rolls_back_new_rows(client):
    assert post(client, error(2)).status_code == 200
    response = post(client, error(1), error(2, reason="Другая причина"), error(3))
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "IMPORT_ERROR_CONFLICT", "source_error_id": 2}}
    items = client.get(BATCH.format(BATCH_ID)).json()["items"]
    assert [item["source_error_id"] for item in items] == [2]
    assert items[0]["reason"] == error(2)["reason"]


def test_conflict_within_request(client):
    response = post(client, error(1), error(1, row_number=11))
    assert response.status_code == 409
    assert response.json()["detail"]["source_error_id"] == 1
    assert client.get(BATCH.format(BATCH_ID)).json()["items"] == []


def test_concurrent_overlapping_replays(client):
    start = Barrier(2)

    def send(items):
        start.wait(timeout=5)
        response = post(client, *items)
        assert response.status_code == 200
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(send, [error(1), error(2)])
        second = executor.submit(send, [error(2), error(1)])
        results = [first.result(timeout=15), second.result(timeout=15)]

    assert sorted(result["inserted"] for result in results) == [0, 2]
    assert sorted(result["existing"] for result in results) == [0, 2]
    assert len(client.get(BATCH.format(BATCH_ID)).json()["items"]) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": []},
        {"errors": [error(1, import_type="CLIENT_HISTORY")]},
        {"errors": [error(1, actor_id=str(uuid4()))]},
        {"errors": [error(1, row_number=-1)]},
        {"errors": [error(1, recorded_at="2026-11-23T12:00:00")]},
        {"errors": [error(1, recorded_at=1795435200)]},
        {"errors": [error(1, recorded_at="1795435200")]},
        {"errors": [error(1, kcr_code=123)]},
        {"errors": [error(1, sheet="x\0")]},
        {"errors": [error(2**63)]},
        {"errors": [error(1)], "source": "OPERATOR_WORK"},
    ],
)
def test_bulk_validation(payload, monkeypatch):
    # Validation rejects the payload before connecting to the database.
    monkeypatch.setenv(
        "ERRORS_HISTORY_DATABASE_URL", "postgresql+psycopg://invalid@127.0.0.1:1/invalid"
    )
    with TestClient(create_app()) as http:
        assert http.post(BULK, json=payload).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        CLIENT.format("123") + "?page=0",
        CLIENT.format("123") + "?import_type=OTHER",
        BATCH.format(BATCH_ID) + "?page_size=101",
        BATCH.format("not-a-uuid"),
    ],
)
def test_read_validation(client, path):
    assert client.get(path).status_code == 422


def test_ready_requires_migration(database):
    with TestClient(create_app()) as http:
        assert http.get("/health/live").json() == {"status": "ok"}
        assert http.get("/health/ready").status_code == 503
        command.upgrade(database, "head")
        assert http.get("/health/ready").json() == {"status": "ok"}
        command.downgrade(database, "base")
        assert http.get("/health/ready").status_code == 503
        command.upgrade(database, "head")
        assert http.get("/health/ready").status_code == 200


def test_database_unavailable(monkeypatch):
    monkeypatch.setenv(
        "ERRORS_HISTORY_DATABASE_URL", "postgresql+psycopg://invalid@127.0.0.1:1/invalid"
    )
    with TestClient(create_app()) as http:
        assert http.get("/health/live").status_code == 200
        assert http.get("/health/ready").json() == {"status": "not_ready"}
        response = post(http, error(1))
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "ERRORS_HISTORY_UNAVAILABLE"}}
