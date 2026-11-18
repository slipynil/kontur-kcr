from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

import operator_work.api as api_module
from operator_work.assignments import Assignments, WorkError

CLIENT = "/internal/v1/clients/AAA00412"
WORK_STATE = f"{CLIENT}/work-state"
QUARTER = "2026_Q4"


def actor(user_id: UUID, role: str = "OPERATOR") -> dict[str, str]:
    return {
        "X-User-Id": str(user_id),
        "X-User-Role": role,
    }


@pytest.fixture
def mock_api(monkeypatch):
    assignments = create_autospec(Assignments, instance=True)
    monkeypatch.setattr(api_module, "Assignments", lambda _engine: assignments)
    monkeypatch.delenv("OPERATOR_WORK_CLIENTS_URL", raising=False)
    database = create_autospec(Engine, instance=True)
    with TestClient(api_module.create_app(database)) as client:
        yield client, assignments


def state_payload(operator_id: UUID | None = None) -> dict:
    assigned_at = datetime(2026, 11, 17, 9, tzinfo=UTC)
    return {
        "kcr_code": "AAA00412",
        "work_quarter": QUARTER,
        "assignment_id": uuid4(),
        "assigned_operator_id": operator_id,
        "assigned_at": assigned_at,
        "stage": "IN_WORK",
        "wait_until": None,
        "wait_source_work_quarter": None,
        "awaiting_balance": False,
    }


def test_work_state_reads_the_current_cycle_without_a_quarter(mock_api):
    client, assignments = mock_api
    operator_id = uuid4()
    assignments.state_current.return_value = state_payload(operator_id)

    response = client.get(WORK_STATE, headers=actor(uuid4()))

    assert response.status_code == 200
    body = response.json()
    assert body["work_quarter"] == QUARTER
    assert body["stage"] == "IN_WORK"
    assert body["assigned_operator_id"] == str(operator_id)
    assert datetime.fromisoformat(body["assigned_at"]) == datetime(2026, 11, 17, 9, tzinfo=UTC)
    assignments.state_current.assert_called_once_with("AAA00412", None)


def test_work_state_accepts_an_explicit_quarter_for_admin(mock_api):
    client, assignments = mock_api
    assignments.state_current.return_value = state_payload()

    response = client.get(
        WORK_STATE, params={"work_quarter": "2026_Q3"}, headers=actor(uuid4(), role="ADMIN")
    )

    assert response.status_code == 200
    assignments.state_current.assert_called_once_with("AAA00412", "2026_Q3")


def test_work_state_rejects_a_malformed_quarter(mock_api):
    client, assignments = mock_api

    response = client.get(WORK_STATE, params={"work_quarter": "2026-Q3"}, headers=actor(uuid4()))

    assert response.status_code == 422
    assignments.state_current.assert_not_called()


def test_work_state_reports_a_missing_cycle(mock_api):
    client, assignments = mock_api
    assignments.state_current.side_effect = WorkError(
        404, "WORK_CYCLE_NOT_FOUND", "Действующий цикл обзвона не найден"
    )

    response = client.get(WORK_STATE, headers=actor(uuid4()))

    assert response.status_code == 404
    assert response.json()["code"] == "WORK_CYCLE_NOT_FOUND"
