"""HTTP integration within Clients; does not pretend Gateway/Import exist."""

from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient

from clients_service.config import Settings
from clients_service.main import create_app

pytestmark = pytest.mark.postgres


def test_import_read_delete_rollback_over_http(database):
    _, config, url = database
    command.upgrade(config, "head")
    actor = str(uuid4())
    settings = Settings(
        database_url=url,
        enable_registry_read=True,
        enable_import_apply=True,
        enable_data_recovery=True,
    )
    with TestClient(create_app(settings)) as client:

        def apply(kind, items, day):
            payload = {
                "batch_id": str(uuid4()),
                "uploaded_by": actor,
                "selected_quarter": "2026_Q2",
                "import_type": kind,
                "snapshot_date": day,
                "items": items,
            }
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
