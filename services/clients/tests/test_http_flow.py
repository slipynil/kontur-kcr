"""HTTP integration within Clients; does not pretend Gateway/Import exist."""

from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from clients_service.config import Settings
from clients_service.main import create_app

pytestmark = pytest.mark.postgres


def test_affected_clients_lists_accepted_codes_and_preserves_quarter_after_deletion(database):
    _, config, url = database
    command.upgrade(config, "head")
    actor = str(uuid4())
    settings = Settings(database_url=url)

    def row(code, quarter, balance):
        return {
            "client": {"kcr_code": code},
            "snapshot": {
                "quarter": quarter,
                "total_documents": 100,
                "unsubmitted_documents": balance,
            },
        }

    with TestClient(create_app(settings)) as client:

        def apply(kind, items):
            batch_id = str(uuid4())
            result = client.post(
                "/internal/v1/import/apply",
                json={
                    "batch_id": batch_id,
                    "uploaded_by": actor,
                    "import_type": kind,
                    "items": items,
                    **(
                        {"selected_quarter": "2026_Q2", "snapshot_date": "2026-04-10"}
                        if kind == "SNAPSHOT"
                        else {}
                    ),
                },
            )
            assert result.status_code == 200, result.text
            return f"/internal/v1/import/{batch_id}", result.json()

        registry, _ = apply(
            "REGISTRY", [{"client": {"kcr_code": code}} for code in ("b", "a", "c")]
        )
        suffix = "/affected-clients"
        assert client.get(registry + suffix).json()["items"] == [
            {"kcr_code": code, "source_quarters": [], "outstanding_documents": None}
            for code in ("a", "b", "c")
        ]

        apply("SNAPSHOT", [row("a", "2026_Q1", 4)])
        batch, report = apply(
            "SNAPSHOT",
            [
                row("b", "2026_Q2", 0),
                row("a", "2026_Q2", 6),
                row("a", "2026_Q1", 5),
                row("unknown", "2026_Q2", 9),
            ],
        )
        assert (report["accepted_rows"], report["rejected_rows"]) == (3, 1)
        path = batch + suffix
        first = client.get(path, params={"limit": 1})
        assert first.status_code == 200 and first.json()["total"] == 2
        assert first.json()["items"] == [
            {
                "kcr_code": "a",
                "source_quarters": ["2026_Q1", "2026_Q2"],
                "outstanding_documents": 11,
            }
        ]
        second = client.get(path, params={"limit": 1, "offset": 1}).json()
        assert second["items"] == [
            {"kcr_code": "b", "source_quarters": ["2026_Q2"], "outstanding_documents": 0}
        ]
        assert client.get(path, params={"limit": 1, "offset": 1}).json() == second
        assert client.get(path, params={"offset": 100}).json()["total"] == 2
        assert client.get(path, params={"limit": 1001}).status_code == 400

        rejected, _ = apply("SNAPSHOT", [row("unknown", "2026_Q2", 9)])
        assert client.get(rejected + suffix).json()["items"] == []
        assert client.get(f"/internal/v1/import/{uuid4()}" + suffix).status_code == 404

        deletion = {"operation_id": str(uuid4()), "requested_by": actor}
        result = client.request("DELETE", "/internal/v1/clients/a/snapshots/2026_Q2", json=deletion)
        assert result.status_code == 200
        assert client.get(path).json()["items"][0] == {
            "kcr_code": "a",
            "source_quarters": ["2026_Q1", "2026_Q2"],
            "outstanding_documents": 5,
        }
        rollback = {"operation_id": str(uuid4()), "requested_by": actor}
        assert client.post(batch + "/rollback", json=rollback).status_code == 200
        assert client.get(path).status_code == 409


def test_affected_clients_paginates_large_batch(database):
    engine, config, url = database
    command.upgrade(config, "head")
    batch_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO clients_import_operations (batch_id, payload_hash, result) "
                "VALUES (:batch_id, 'test', '{}')"
            ),
            {"batch_id": batch_id},
        )
        connection.execute(
            text("""INSERT INTO clients_import_undo (batch_id, kcr_code, previous_state)
                SELECT :batch_id, 'client-' || lpad(number::text, 4, '0'), '{}'
                FROM generate_series(1, 1001) AS entries(number)"""),
            {"batch_id": batch_id},
        )
    settings = Settings(database_url=url)
    with TestClient(create_app(settings)) as client:
        path = f"/internal/v1/import/{batch_id}/affected-clients"
        first = client.get(path).json()
        second = client.get(path, params={"offset": 1000}).json()
        assert len(first["items"]) == 1000 and len(second["items"]) == 1
        assert first["items"][-1]["kcr_code"] == "client-1000"
        assert second["items"][0]["kcr_code"] == "client-1001"
        assert first["total"] == second["total"] == 1001


def test_import_read_delete_rollback_over_http(database):
    _, config, url = database
    command.upgrade(config, "head")
    actor = str(uuid4())
    settings = Settings(database_url=url)
    with TestClient(create_app(settings)) as client:

        def apply(kind, items, day):
            payload = {
                "batch_id": str(uuid4()),
                "uploaded_by": actor,
                "import_type": kind,
                "items": items,
            }
            if kind == "SNAPSHOT":
                payload.update(selected_quarter="2026_Q2", snapshot_date=day)
            response = client.post("/internal/v1/import/apply", json=payload)
            assert response.status_code == 200, response.text
            assert response.json()["rejected_rows"] == 0
            return payload

        def rows(first, second):
            return [
                {
                    "client": {"kcr_code": "example"},
                    "snapshot": {
                        "quarter": quarter,
                        "total_documents": 100,
                        "unsubmitted_documents": count,
                    },
                }
                for quarter, count in [("2026_Q1", first), ("2026_Q2", second)]
            ]

        assert client.get("/health/ready").status_code == 200
        apply("REGISTRY", [{"client": {"kcr_code": "example", "tags": ["VIP"]}}], "2026-04-01")
        apply("SNAPSHOT", rows(10, 20), "2026-04-10")
        changed = apply("SNAPSHOT", rows(7, 15), "2026-04-20")
        assert client.post("/internal/v1/import/apply", json=changed).json()["accepted_rows"] == 2
        path = "/internal/v1/clients/example"
        card = client.get(path + "?quarter=2026_Q2&snapshots_limit=1").json()
        assert card["outstanding_documents"] == 22
        assert card["dynamics"] == "GROWING"
        assert len(card["snapshots"]) == 1
        history = client.get(path + "/snapshots/history").json()
        assert history["total"] == 4

        deletion = {"operation_id": str(uuid4()), "requested_by": actor}
        deleted = client.request("DELETE", path + "/snapshots/2026_Q1", json=deletion)
        assert deleted.status_code == 200 and deleted.json()["local_only"]
        assert client.get(path).json()["outstanding_documents"] == 15
        assert client.get(path + "/snapshots/history?quarter=2026_Q1").json()["total"] == 0

        restore = {"operation_id": str(uuid4()), "requested_by": actor}
        response = client.post(f"/internal/v1/import/{changed['batch_id']}/rollback", json=restore)
        assert response.status_code == 200 and response.json()["local_only"]
        card = client.get(path).json()
        assert card["outstanding_documents"] == 30
        assert card["tags"] == ["VIP"]
        assert client.get(path + "/snapshots/history").json()["total"] == 2
        # Replay of the previous delete command does not delete restored data.
        replay = client.request("DELETE", path + "/snapshots/2026_Q1", json=deletion)
        assert replay.json() == deleted.json()
        assert client.get(path).json()["outstanding_documents"] == 30
