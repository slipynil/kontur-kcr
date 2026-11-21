from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

import operator_work.api as api_module
from operator_work.assignments import Assignments, WorkError

CLIENT = "/internal/v1/clients/AAA00412"
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


def test_take_returns_the_new_assignment_immediately(mock_api):
    client, assignments = mock_api
    operator_id, previous_id, assignment_id, request_id = (uuid4() for _ in range(4))
    assigned_at = datetime(2026, 11, 21, 9, tzinfo=UTC)
    assignments.take.return_value = {
        "kcr_code": "AAA00412",
        "work_quarter": QUARTER,
        "assignment_id": assignment_id,
        "assigned_operator_id": operator_id,
        "assigned_at": assigned_at,
    }

    response = client.post(
        f"{CLIENT}/take",
        params={"work_quarter": QUARTER},
        headers=actor(operator_id),
        json={"expected_assignment_id": str(previous_id), "request_id": str(request_id)},
    )
    assert response.status_code == 200
    assert response.json()["assignment_id"] == str(assignment_id)
    assert response.json()["assigned_operator_id"] == str(operator_id)
    assignments.take.assert_called_once_with(
        "AAA00412", QUARTER, operator_id, previous_id, request_id
    )


def test_take_serializes_assignment_conflict(mock_api):
    client, assignments = mock_api
    operator_id, request_id = uuid4(), uuid4()
    assignments.take.side_effect = WorkError(
        409, "ASSIGNMENT_CHANGED", "Ответственный изменился. Обновите карточку"
    )

    response = client.post(
        f"{CLIENT}/take",
        params={"work_quarter": QUARTER},
        headers=actor(operator_id),
        json={"expected_assignment_id": None, "request_id": str(request_id)},
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "ASSIGNMENT_CHANGED",
        "message": "Ответственный изменился. Обновите карточку",
    }
    assignments.take.assert_called_once_with("AAA00412", QUARTER, operator_id, None, request_id)


def test_take_requires_operator_role(mock_api):
    client, assignments = mock_api
    response = client.post(
        f"{CLIENT}/take",
        params={"work_quarter": QUARTER},
        headers=actor(uuid4(), "ADMIN"),
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    )

    assert response.status_code == 403
    assignments.take.assert_not_called()
