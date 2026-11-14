from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

import operator_work.api as api_module
from operator_work.assignments import Assignments, WorkError

CLIENT = "/internal/v1/clients/AAA00412"
NOTIFICATIONS = "/internal/v1/reassignment-notifications"
QUARTER = "2026_Q4"
TOKEN = "test-service-token"


def actor(user_id: UUID, role: str = "OPERATOR") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-Id": str(user_id),
        "X-User-Role": role,
    }


@pytest.fixture
def mock_api(monkeypatch):
    assignments = create_autospec(Assignments, instance=True)
    monkeypatch.setattr(api_module, "Assignments", lambda _engine: assignments)
    monkeypatch.delenv("OPERATOR_WORK_CLIENTS_URL", raising=False)
    database = create_autospec(Engine, instance=True)
    with TestClient(api_module.create_app(database, TOKEN)) as client:
        yield client, assignments


def test_operator_can_request_reassignment_and_owner_can_reject(mock_api):
    client, assignments = mock_api
    owner_id, operator_id, assignment_id, request_id = (uuid4() for _ in range(4))
    created = datetime(2026, 11, 14, 9, tzinfo=UTC)
    request = {
        "id": request_id,
        "kcr_code": "AAA00412",
        "work_quarter": QUARTER,
        "expected_assignment_id": assignment_id,
        "owner_id": owner_id,
        "requester_id": operator_id,
        "created_at": created,
        "expires_at": created + timedelta(hours=1),
        "status": "PENDING",
    }
    assignments.take.return_value = (202, request)
    assignments.notifications.return_value = [request]
    assignments.reject.return_value = (200, {**request, "status": "REJECTED"})

    pending = client.post(
        f"{CLIENT}/take",
        params={"work_quarter": QUARTER},
        headers=actor(operator_id),
        json={"expected_assignment_id": str(assignment_id), "request_id": str(request_id)},
    )
    assert pending.status_code == 202
    pending_request = pending.json()
    assert pending_request["id"] == str(request_id)
    assert pending_request["owner_id"] == str(owner_id)
    assert pending_request["requester_id"] == str(operator_id)
    assert pending_request["status"] == "PENDING"
    assert datetime.fromisoformat(pending_request["expires_at"]) == created + timedelta(hours=1)
    assignments.take.assert_called_once_with(
        "AAA00412", QUARTER, operator_id, assignment_id, request_id
    )

    inbox = client.get(NOTIFICATIONS, headers=actor(owner_id))
    assert inbox.status_code == 200
    assert inbox.json() == {"items": [pending_request]}
    assignments.notifications.assert_called_once_with(owner_id, 50, 0)

    rejected = client.post(f"{NOTIFICATIONS}/{request_id}/reject", headers=actor(owner_id))
    assert rejected.status_code == 200
    assert rejected.json() == {**pending_request, "status": "REJECTED"}
    assignments.reject.assert_called_once_with(request_id, owner_id)


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


@pytest.mark.parametrize(
    ("role", "token", "status"), [("ADMIN", TOKEN, 403), ("OPERATOR", "bad", 401)]
)
def test_take_requires_service_token_and_operator_role(mock_api, role, token, status):
    client, assignments = mock_api
    headers = actor(uuid4(), role)
    headers["Authorization"] = f"Bearer {token}"

    response = client.post(
        f"{CLIENT}/take",
        params={"work_quarter": QUARTER},
        headers=headers,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    )

    assert response.status_code == status
    assignments.take.assert_not_called()
