from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os import getenv
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import operator_work.api as api_module
from operator_work.api import create_app
from operator_work.assignments import Assignments
from operator_work.wait_expiry import WaitExpiry

BASE = "/internal/v1"
CODE = "AAA00412"
QUARTER = "2026_Q3"
CLIENT = f"{BASE}/clients/{CODE}"
CYCLE = {"work_quarter": QUARTER}
NOTIFICATIONS = f"{BASE}/reassignment-notifications"


def identity(role="OPERATOR"):
    return {
        "Authorization": "Bearer local-test",
        "X-User-Role": role,
        "X-User-Id": str(uuid4()),
    }


@pytest.fixture
def operators():
    return identity(), identity(), identity()


@pytest.fixture
def database(monkeypatch):
    from alembic import command
    from alembic.config import Config

    url = getenv("OPERATOR_WORK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set OPERATOR_WORK_TEST_DATABASE_URL to a disposable PostgreSQL database")
    monkeypatch.setenv("OPERATOR_WORK_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                """TRUNCATE client_reassignment_requests, client_events, client_stages,
                   client_assignments, client_work_cycles, client_cleanup_operations,
                   applied_work_snapshot_parts, applied_work_snapshots"""
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def api(database):
    with TestClient(create_app(database, "local-test")) as client:
        yield client


def take(api, operator, expected=None, request_id=None):
    return api.post(
        f"{CLIENT}/take",
        params=CYCLE,
        headers=operator,
        json={"expected_assignment_id": expected, "request_id": request_id or str(uuid4())},
    )


def publish(api, quarter=QUARTER):
    response = api.put(
        f"{CLIENT}/publication", params={"work_quarter": quarter}, headers=identity("ADMIN")
    )
    assert response.status_code == 204


def owner(api, operator):
    return api.get(f"{CLIENT}/assignment", params=CYCLE, headers=operator).json()[
        "assigned_operator_id"
    ]


def request_transfer(api, a, b):
    publish(api)
    taken = take(api, a)
    assert taken.status_code == 200
    request = take(api, b, taken.json()["assignment_id"])
    assert request.status_code == 202
    return taken.json(), request.json()


def set_due(database, request_id):
    with database.begin() as db:
        db.execute(
            text(
                """UPDATE client_reassignment_requests
                   SET created_at = now() - INTERVAL '1 hour', expires_at = now()
                   WHERE id = :id"""
            ),
            {"id": request_id},
        )


def test_publish_and_concurrent_take(api, operators, database):
    a, b, _ = operators
    assert take(api, a).status_code == 404
    assert api.get(f"{BASE}/pool", params=CYCLE, headers=a).json() == {"items": []}
    assert api.put(f"{CLIENT}/publication", params=CYCLE, headers=a).status_code == 403
    assert take(api, {**a, "Authorization": "Bearer invalid"}).status_code == 401
    publish(api)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda actor: take(api, actor), (a, b)))
    assert sorted(response.status_code for response in results) == [200, 409]
    conflict = next(response for response in results if response.status_code == 409)
    assert conflict.json()["code"] == "ASSIGNMENT_CHANGED"
    winner = next(response for response in results if response.status_code == 200).json()
    assert api.get(f"{BASE}/pool", params=CYCLE, headers=a).json()["items"] == [
        {"kcr_code": CODE, "assigned_operator_id": winner["assigned_operator_id"]}
    ]
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM client_assignments")) == 1
        assert db.scalar(text("SELECT count(*) FROM client_events")) == 1


def test_only_owner_can_reject_and_replay_preserves_decision(api, operators, database):
    a, b, c = operators
    assigned, request = request_transfer(api, a, b)
    request_id = request["id"]
    reject = f"{NOTIFICATIONS}/{request_id}/reject"
    inbox = api.get(NOTIFICATIONS, headers=a).json()["items"]
    assert inbox == [request]
    assert api.get(NOTIFICATIONS, headers=c).json()["items"] == []
    assert api.post(reject, headers=b).status_code == 404
    assert api.post(reject, headers=c).status_code == 404
    assert owner(api, a) == a["X-User-Id"]
    assert take(api, c, assigned["assignment_id"]).json()["code"] == "REASSIGNMENT_PENDING"
    assert api.post(reject, headers=a).json()["status"] == "REJECTED"
    assert api.post(reject, headers=a).json()["status"] == "REJECTED"
    assert take(api, b, assigned["assignment_id"], request_id).json()["status"] == "REJECTED"
    Assignments(database).settle_due()
    assert owner(api, a) == a["X-User-Id"]


@pytest.mark.parametrize("settle_with", ["worker", "late_reject"])
def test_timeout_transfers_once(api, operators, database, settle_with):
    a, b, _ = operators
    _, request = request_transfer(api, a, b)
    set_due(database, request["id"])
    if settle_with == "worker":
        Assignments(database).settle_due()
    else:
        response = api.post(f"{NOTIFICATIONS}/{request['id']}/reject", headers=a)
        assert response.status_code == 409
    Assignments(database).settle_due()
    assert owner(api, b) == b["X-User-Id"]
    assert api.get(NOTIFICATIONS, headers=b).json()["items"][0]["status"] == "TRANSFERRED"
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM client_assignments")) == 2
        assert db.scalar(text("SELECT count(*) FROM client_events")) == 2


@pytest.mark.parametrize("change", ["wait", "quarter"])
def test_pending_request_cannot_override_new_protection(api, operators, database, change):
    a, b, _ = operators
    _, request = request_transfer(api, a, b)
    if change == "wait":
        with database.begin() as db:
            db.execute(text("UPDATE client_work_cycles SET wait_until = CURRENT_DATE + 5"))
    else:
        publish(api, quarter="2026_Q4")

    set_due(database, request["id"])
    Assignments(database).settle_due()
    assert api.get(NOTIFICATIONS, headers=b).json()["items"][0]["status"] == "CANCELLED"
    if change == "wait":
        assert owner(api, a) == a["X-User-Id"]
    else:
        assert take(api, b).json()["code"] == "INVALID_STATE"


def test_wait_blocks_new_request_and_carries_to_new_quarter(api, operators, database):
    a, b, _ = operators
    publish(api)
    assigned = take(api, a).json()
    with database.begin() as db:
        db.execute(text("UPDATE client_work_cycles SET wait_until = CURRENT_DATE + 5"))
    assert take(api, b, assigned["assignment_id"]).json()["code"] == "CLIENT_WAIT_DEADLINE_ACTIVE"
    publish(api, quarter="2026_Q4")
    new_state = api.get(
        f"{CLIENT}/assignment", params={"work_quarter": "2026_Q4"}, headers=b
    ).json()
    assert new_state["assigned_operator_id"] == a["X-User-Id"]


def test_state_settles_due_request_and_publication_is_idempotent(api, operators, database):
    a, b, c = operators
    assigned, request = request_transfer(api, a, b)
    publish(api)
    assert (
        take(api, a, assigned["assignment_id"]).json()["assignment_id"] == assigned["assignment_id"]
    )
    assert (
        take(api, c, assigned["assignment_id"], request["id"]).json()["code"]
        == "REQUEST_ID_CONFLICT"
    )
    set_due(database, request["id"])
    assert owner(api, b) == b["X-User-Id"]
    replay = take(api, b, assigned["assignment_id"], request["id"])
    assert replay.status_code == 200
    assert replay.json()["status"] == "TRANSFERRED"


def call_payload(assignment, **overrides):
    return {
        "event_type": "CALL_RESULT",
        "request_id": str(uuid4()),
        "work_quarter": QUARTER,
        "expected_assignment_id": assignment["assignment_id"],
        "call_result": "AGREEMENT",
        "comment_text": "Передаст оригиналы курьером",
        "promised_date": "2036-11-17",
        "wait_until": "2036-11-21",
        **overrides,
    }


def record_call(api, actor, payload):
    return api.post(f"{CLIENT}/events", headers=actor, json=payload)


def test_call_and_wait_cancel_pending_request_and_replay(api, operators):
    a, b, _ = operators
    assigned, _ = request_transfer(api, a, b)
    payload = call_payload(assigned)
    response = record_call(api, a, payload)
    assert response.status_code == 201
    event = response.json()
    assert event["author_id"] == a["X-User-Id"]
    assert event["promised_date"] == payload["promised_date"]
    assert event["wait_until"] == payload["wait_until"]
    assert event["wait_applied"] is True
    assert api.get(NOTIFICATIONS, headers=b).json()["items"][0]["status"] == "CANCELLED"

    replay = record_call(api, a, payload)
    assert replay.status_code == 200
    assert replay.json() == event
    conflict = record_call(api, a, {**payload, "comment_text": "Другая запись"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "REQUEST_ID_CONFLICT"
    assert take(api, b, assigned["assignment_id"]).json()["code"] == "CLIENT_WAIT_DEADLINE_ACTIVE"

    history = api.get(f"{CLIENT}/events", headers=a, params={"page_size": 2}).json()
    assert history["total"] == 3
    assert {item["event_type"] for item in history["items"]} == {"CALL_RESULT", "STAGE_CHANGE"}
    older = api.get(
        f"{CLIENT}/events", headers=identity("ADMIN"), params={"page": 2, "page_size": 2}
    ).json()
    assert older["items"][0]["event_type"] == "OPERATOR_ASSIGN"
    publish(api, quarter="2026_Q4")
    state = api.get(f"{CLIENT}/assignment", headers=a, params={"work_quarter": "2026_Q4"}).json()
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["wait_until"] == payload["wait_until"]
    assert state["assigned_operator_id"] == a["X-User-Id"]
    assert record_call(api, a, payload).json() == event
    assert (
        record_call(api, a, {**payload, "request_id": str(uuid4())}).json()["code"]
        == "INVALID_STATE"
    )


def test_stale_form_records_call_without_changing_new_owner_or_wait(api, operators, database):
    a, b, c = operators
    assigned, request = request_transfer(api, a, b)
    set_due(database, request["id"])
    payload = call_payload(assigned)
    response = record_call(api, a, payload)
    assert response.status_code == 201
    assert response.json()["wait_applied"] is False
    assert response.json()["wait_until"] is None
    assert response.json()["author_id"] == a["X-User-Id"]
    assert record_call(api, a, payload).status_code == 200
    assert record_call(api, c, call_payload(assigned)).json()["code"] == "ASSIGNMENT_CHANGED"
    state = api.get(f"{CLIENT}/assignment", headers=b, params=CYCLE).json()
    assert state["assigned_operator_id"] == b["X-User-Id"]
    assert state["wait_until"] is None
    assert state["stage"] == "IN_WORK"


def test_invalid_wait_does_not_save_partial_call(api, operators):
    a, _, _ = operators
    assert api.get(f"{CLIENT}/events", headers=a).status_code == 404
    publish(api)
    assigned = take(api, a).json()
    payload = call_payload(assigned, wait_until="2000-01-01")
    assert record_call(api, identity("ADMIN"), payload).status_code == 403
    response = record_call(api, a, payload)
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_WAIT_DATE"
    history = api.get(f"{CLIENT}/events", headers=a).json()
    assert history["total"] == 1
    assert history["items"][0]["event_type"] == "OPERATOR_ASSIGN"
    assert record_call(api, a, {**payload, "wait_until": None}).status_code == 201


def enter_wait(api, database, *, expired=True):
    a = identity()
    publish(api)
    assigned = take(api, a).json()
    record = record_call(api, a, call_payload(assigned, wait_until="2036-11-21"))
    assert record.status_code == 201
    if expired:
        with database.begin() as db:
            db.execute(
                text("""UPDATE client_work_cycles
                        SET wait_until = (statement_timestamp() AT TIME ZONE 'Europe/Moscow')::date
                        WHERE kcr_code = :code"""),
                {"code": CODE},
            )
    return a, assigned


def test_expired_wait_returns_to_pool_once_if_clients_confirms_balance(api, database):
    a, assigned = enter_wait(api, database)
    b = identity()
    assert api.get(f"{BASE}/pool", headers=b, params=CYCLE).json()["items"] == []
    blocked = take(api, b, assigned["assignment_id"])
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CLIENT_BALANCE_UNCONFIRMED"

    def clients(request):
        assert request.method == "GET"
        assert request.url.path == f"/internal/v1/clients/{CODE}"
        return httpx.Response(200, json={"kcr_code": CODE, "outstanding_documents": 3})

    with httpx.Client(
        base_url="http://clients.test", transport=httpx.MockTransport(clients)
    ) as client:
        worker = WaitExpiry(database, client)
        worker.process()
        worker.process()

    state = api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
    assert state["assigned_operator_id"] is None
    assert state["stage"] == "POOL"
    assert state["wait_until"] is None
    assert state["awaiting_balance"] is False
    assert api.get(f"{BASE}/pool", headers=b, params=CYCLE).json()["items"] == [
        {"kcr_code": CODE, "assigned_operator_id": None},
    ]
    history = api.get(f"{CLIENT}/events", headers=b).json()["items"]
    assert sum(event["stage"] == "POOL" for event in history) == 1
    assert take(api, b).status_code == 200


@pytest.mark.parametrize("reply", ["zero", "unavailable"])
def test_expired_wait_stays_out_of_pool_until_positive_reply(api, database, reply):
    a, assigned = enter_wait(api, database)
    b = identity()
    with httpx.Client(
        base_url="http://clients.test",
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(200, json={"kcr_code": CODE, "outstanding_documents": 0})
                if reply == "zero"
                else httpx.Response(503)
            )
        ),
    ) as client:
        WaitExpiry(database, client).process()

    state = api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
    assert state["assignment_id"] == assigned["assignment_id"]
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["awaiting_balance"] is True
    assert api.get(f"{BASE}/pool", headers=b, params=CYCLE).json()["items"] == []
    assert take(api, b, assigned["assignment_id"]).json()["code"] == "CLIENT_BALANCE_UNCONFIRMED"
    assert api.get(f"{CLIENT}/events", headers=a).json()["total"] == 3

    # A new quarter must not bypass the unfinished check.
    publish(api, quarter="2026_Q4")
    quarter = {"work_quarter": "2026_Q4"}
    assert api.get(f"{BASE}/pool", headers=b, params=quarter).json()["items"] == []
    if reply == "unavailable":
        with httpx.Client(
            base_url="http://clients.test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"kcr_code": CODE, "outstanding_documents": 1})
            ),
        ) as client:
            WaitExpiry(database, client).process()
        assert api.get(f"{BASE}/pool", headers=b, params=quarter).json()["items"] == [
            {"kcr_code": CODE, "assigned_operator_id": None},
        ]


def test_new_wait_wins_over_a_late_positive_clients_response(api, database):
    a, assigned = enter_wait(api, database)

    def clients(_):
        renewed = record_call(api, a, call_payload(assigned, wait_until="2036-11-22"))
        assert renewed.status_code == 201
        return httpx.Response(200, json={"kcr_code": CODE, "outstanding_documents": 1})

    with httpx.Client(
        base_url="http://clients.test", transport=httpx.MockTransport(clients)
    ) as client:
        WaitExpiry(database, client).process()

    state = api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
    assert state["assignment_id"] == assigned["assignment_id"]
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["wait_until"] == "2036-11-22"


def test_wait_worker_runs_on_startup(api, database, monkeypatch):
    a, _ = enter_wait(api, database)
    monkeypatch.setenv("OPERATOR_WORK_CLIENTS_URL", "http://clients.test")
    fake_clients = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"kcr_code": CODE, "outstanding_documents": 1}),
    )
    monkeypatch.setattr(
        api_module,
        "httpx",
        SimpleNamespace(Client=lambda **opts: httpx.Client(**opts, transport=fake_clients)),
    )
    with TestClient(create_app(database, "local-test")) as client:
        deadline = monotonic() + 3
        while monotonic() < deadline:
            state = client.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
            if state["stage"] == "POOL":
                break
            sleep(0.01)
        else:
            pytest.fail("The startup worker did not return the expired client to the pool")


def historical_comment(**overrides):
    return {
        "event_type": "HISTORICAL_COMMENT",
        "request_id": str(uuid4()),
        "comment_text": "Клиент ранее согласовал передачу оригиналов",
        "historical_event_date": "2025-09-01T14:30:00+03:00",
        **overrides,
    }


def test_historical_comment_preserves_wait_and_is_idempotent(api, operators, database):
    a, b, _ = operators
    publish(api)
    assigned = take(api, a).json()
    assert record_call(api, a, call_payload(assigned)).status_code == 201
    before = api.get(f"{CLIENT}/assignment", params=CYCLE, headers=a).json()
    payload = historical_comment()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: record_call(api, b, payload), range(2)))
    assert sorted(result.status_code for result in results) == [200, 201]
    event = results[0].json()
    assert event == results[1].json()
    assert event["id"] == payload["request_id"]
    assert event["event_type"] == "HISTORICAL_COMMENT"
    assert event["author_id"] == b["X-User-Id"]
    assert event["comment_text"] == payload["comment_text"]
    assert event["work_quarter"] is None
    assert datetime.fromisoformat(event["historical_event_date"]) == datetime.fromisoformat(
        payload["historical_event_date"]
    )
    assert datetime.fromisoformat(event["created_at"]) > datetime.fromisoformat(
        event["historical_event_date"]
    )

    assert record_call(api, a, payload).json()["code"] == "REQUEST_ID_CONFLICT"
    assert record_call(api, b, {**payload, "comment_text": "Другой текст"}).status_code == 409
    history = api.get(f"{CLIENT}/events", headers=a).json()
    assert sum(item["id"] == event["id"] for item in history["items"]) == 1
    after = api.get(f"{CLIENT}/assignment", params=CYCLE, headers=a).json()
    assert before == after
    assert after["assigned_operator_id"] == a["X-User-Id"]
    assert after["stage"] == "WAIT_DEADLINE"
    assert after["wait_until"] == "2036-11-21"
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM client_stages")) == 3
        assert db.scalar(text("SELECT count(*) FROM client_assignments")) == 1


def test_historical_comment_validates_date_and_client_access(api, operators):
    a, _, _ = operators
    assert record_call(api, a, historical_comment()).status_code == 404
    publish(api)
    assert record_call(api, identity("ADMIN"), historical_comment()).status_code == 403
    assert record_call(api, a, historical_comment(comment_text="  ")).status_code == 422
    assert (
        record_call(
            api,
            a,
            historical_comment(
                historical_event_date="2025-09-01T14:30:00",
            ),
        ).status_code
        == 422
    )
    future = record_call(api, a, historical_comment(historical_event_date="2099-01-01T00:00:00Z"))
    assert future.status_code == 422
    assert future.json()["code"] == "INVALID_EVENT_DATE"
    assert record_call(api, a, historical_comment(work_quarter="2026_Q1")).status_code == 409
    assert api.get(f"{CLIENT}/events", headers=a).json()["items"] == []


def publish_batch(api, codes, *, actor=None, quarter=QUARTER):
    return api.put(
        f"{BASE}/pool/publication",
        params={"work_quarter": quarter},
        headers=actor or identity("ADMIN"),
        json={"codes": codes},
    )


def test_batch_publication_replay_preserves_assignment_wait_and_pending(api, operators, database):
    a, b, _ = operators
    other = "AAA02210"
    assert publish_batch(api, [CODE, other], actor=a).status_code == 403
    assert publish_batch(api, [CODE, " "]).status_code == 422
    assert publish_batch(api, []).status_code == 422
    assert api.get(f"{BASE}/pool", headers=a, params=CYCLE).json()["items"] == []

    assert publish_batch(api, [other, CODE, CODE]).status_code == 204
    assigned = take(api, a).json()
    assert record_call(api, a, call_payload(assigned)).status_code == 201
    other_assignment = api.post(
        f"{BASE}/clients/{other}/take",
        params=CYCLE,
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    ).json()
    pending = api.post(
        f"{BASE}/clients/{other}/take",
        params=CYCLE,
        headers=b,
        json={
            "expected_assignment_id": other_assignment["assignment_id"],
            "request_id": str(uuid4()),
        },
    )
    assert pending.status_code == 202

    before = api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
    assert before["stage"] == "WAIT_DEADLINE"
    assert before["wait_until"] == "2036-11-21"
    with database.connect() as db:
        stage_count = db.scalar(text("SELECT count(*) FROM client_stages"))

    assert publish_batch(api, [CODE, other]).status_code == 204
    assert api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json() == before
    assert api.get(NOTIFICATIONS, headers=b).json()["items"] == [pending.json()]
    assert api.get(f"{BASE}/pool", headers=b, params=CYCLE).json()["items"] == [
        {"kcr_code": CODE, "assigned_operator_id": a["X-User-Id"]},
        {"kcr_code": other, "assigned_operator_id": a["X-User-Id"]},
    ]
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM client_stages")) == stage_count
        assert db.scalar(text("SELECT count(*) FROM client_work_cycles")) == 2


def test_batch_publication_rolls_back_first_client_when_second_conflicts(api, operators, database):
    a, _, _ = operators
    other = "AAA02210"
    assert publish_batch(api, [other], quarter="2026_Q4").status_code == 204
    # CODE sorts first and its cycle is written before the conflict on the second client.
    response = publish_batch(api, [other, CODE])
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_STATE"
    assert other in response.json()["message"]
    assert api.get(f"{BASE}/pool", headers=a, params=CYCLE).json()["items"] == []
    assert api.get(f"{BASE}/pool", headers=a, params={"work_quarter": "2026_Q4"}).json()[
        "items"
    ] == [{"kcr_code": other, "assigned_operator_id": None}]
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM client_work_cycles")) == 1
        assert db.scalar(text("SELECT count(*) FROM client_stages")) == 1


def cleanup(api, actor, payload):
    return api.post(f"{BASE}/data-cleanup", headers=actor, json=payload)


def client_work(database, code):
    queries = (
        "SELECT * FROM client_work_cycles WHERE kcr_code = :code ORDER BY work_quarter",
        "SELECT * FROM client_assignments WHERE kcr_code = :code ORDER BY id",
        "SELECT * FROM client_stages WHERE kcr_code = :code ORDER BY id",
        "SELECT * FROM client_events WHERE kcr_code = :code ORDER BY id",
        "SELECT * FROM client_reassignment_requests WHERE kcr_code = :code ORDER BY id",
    )
    with database.connect() as db:
        return [db.execute(text(query), {"code": code}).all() for query in queries]


def test_full_cleanup_is_idempotent_and_leaves_other_clients_intact(api, operators, database):
    a, b, _ = operators
    admin = identity("ADMIN")
    _, past_request = request_transfer(api, a, b)
    quarter = {"work_quarter": "2026_Q4"}
    publish(api, quarter=quarter["work_quarter"])
    assigned = api.post(
        f"{CLIENT}/take",
        params=quarter,
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    ).json()
    assert (
        record_call(
            api, a, call_payload(assigned, work_quarter=quarter["work_quarter"], wait_until=None)
        ).status_code
        == 201
    )
    comment = record_call(api, a, historical_comment())
    assert comment.status_code == 201 and comment.json()["work_quarter"] is None
    active_request = api.post(
        f"{CLIENT}/take",
        params=quarter,
        headers=b,
        json={"expected_assignment_id": assigned["assignment_id"], "request_id": str(uuid4())},
    )
    assert active_request.status_code == 202

    other = "AAA02210"
    other_path = f"{BASE}/clients/{other}"
    assert publish_batch(api, [other], quarter=quarter["work_quarter"]).status_code == 204
    other_assignment = api.post(
        f"{other_path}/take",
        params=quarter,
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    ).json()
    other_request = api.post(
        f"{other_path}/take",
        params=quarter,
        headers=b,
        json={
            "expected_assignment_id": other_assignment["assignment_id"],
            "request_id": str(uuid4()),
        },
    )
    assert other_request.status_code == 202
    assert api.post(f"{other_path}/events", headers=a, json=historical_comment()).status_code == 201
    other_before = client_work(database, other)
    target_before = client_work(database, CODE)
    assert len(target_before[0]) == 2 and all(target_before)
    assert {row.id for row in target_before[-1]} == {
        UUID(past_request["id"]),
        UUID(active_request.json()["id"]),
    }

    payload = {"operation_id": str(uuid4()), "kcr_code": CODE, "scope": "CLIENT"}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: cleanup(api, admin, payload), range(2)))
    for response in responses:
        assert response.status_code == 200
        assert response.json() == {**payload, "status": "COMPLETED"}

    assert client_work(database, CODE) == [[], [], [], [], []]
    assert client_work(database, other) == other_before
    assert api.get(f"{CLIENT}/events", headers=a).status_code == 404
    assert api.get(f"{CLIENT}/assignment", params=quarter, headers=a).status_code == 404
    assert api.get(NOTIFICATIONS, headers=b).json()["items"] == [other_request.json()]
    with database.connect() as db:
        journal = db.execute(text("SELECT * FROM client_cleanup_operations")).mappings().one()
        assert str(journal["operation_id"]) == payload["operation_id"]
        assert journal["kcr_code"] == CODE
        assert str(journal["actor_id"]) == admin["X-User-Id"]
        assert journal["recorded_at"].tzinfo is not None

    assert (
        cleanup(api, admin, {**payload, "kcr_code": other}).json()["code"]
        == "OPERATION_ID_CONFLICT"
    )
    assert cleanup(api, identity("ADMIN"), payload).status_code == 409
    publish(api, quarter=quarter["work_quarter"])
    recreated = client_work(database, CODE)
    assert cleanup(api, admin, payload).status_code == 200
    assert client_work(database, CODE) == recreated
    assert client_work(database, other) == other_before


def test_cleanup_requires_admin_and_full_client_scope(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    publish(api)
    before = client_work(database, CODE)
    payload = {"operation_id": str(uuid4()), "kcr_code": CODE, "scope": "CLIENT"}
    assert cleanup(api, a, payload).status_code == 403
    assert cleanup(api, {**admin, "Authorization": "Bearer invalid"}, payload).status_code == 401
    for scope in ("QUARTER", "BATCH"):
        assert cleanup(api, admin, {**payload, "scope": scope}).status_code == 422
    assert cleanup(api, admin, {**payload, "quarter": QUARTER}).status_code == 422
    assert client_work(database, CODE) == before


def test_cleanup_error_rolls_back_common_event_deletion_and_journal(api, operators, database):
    a, _, _ = operators
    publish(api)
    assert record_call(api, a, historical_comment()).status_code == 201
    before = client_work(database, CODE)
    admin = identity("ADMIN")
    payload = {"operation_id": str(uuid4()), "kcr_code": CODE, "scope": "CLIENT"}
    with database.begin() as db:
        db.execute(
            text(
                """CREATE TABLE test_cleanup_guard (
                   kcr_code TEXT NOT NULL, work_quarter TEXT NOT NULL,
                   FOREIGN KEY (kcr_code, work_quarter)
                       REFERENCES client_work_cycles (kcr_code, work_quarter))"""
            )
        )
        db.execute(
            text("INSERT INTO test_cleanup_guard VALUES (:code, :quarter)"),
            {"code": CODE, "quarter": QUARTER},
        )
    try:
        with pytest.raises(IntegrityError):
            cleanup(api, admin, payload)
        assert client_work(database, CODE) == before
        with database.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM client_cleanup_operations")) == 0
    finally:
        with database.begin() as db:
            db.execute(text("DROP TABLE test_cleanup_guard"))

    assert cleanup(api, admin, payload).status_code == 200
    assert client_work(database, CODE) == [[], [], [], [], []]


def apply_snapshot(api, balances, *, quarter=QUARTER, batch_id=None, actor=None, delivery=None):
    return api.put(
        f"{BASE}/cycles/apply-snapshot",
        params={"work_quarter": quarter},
        headers=actor or identity("ADMIN"),
        json={
            "batch_id": str(batch_id or uuid4()),
            **({"delivery": delivery} if delivery is not None else {}),
            "clients": [
                {"kcr_code": code, "outstanding_documents": outstanding}
                for code, outstanding in balances
            ],
        },
    )


def test_snapshot_prepares_unpublished_cycle_and_replay_preserves_work(api, operators, database):
    a, b, _ = operators
    other = "AAA02210"
    batch_id = uuid4()
    balances = [(CODE, 4), (other, 0)]
    assert apply_snapshot(api, balances, actor=a).status_code == 403
    assert apply_snapshot(api, [(CODE, 4), (CODE, 4)]).json()["code"] == "DUPLICATE_KCR_CODE"
    assert apply_snapshot(api, [(CODE, -1)]).status_code == 422
    assert apply_snapshot(api, balances, batch_id=batch_id).status_code == 204

    cycles, assignments, stages, *_ = client_work(database, CODE)
    assert len(cycles) == 1 and cycles[0].source_import_batch_id == batch_id
    assert cycles[0].published_at is None
    assert not assignments and [stage.stage for stage in stages] == ["POOL"]
    assert client_work(database, other) == [[], [], [], [], []]
    assert api.get(f"{BASE}/pool", headers=a, params=CYCLE).json()["items"] == []
    assert take(api, a).json()["code"] == "CLIENT_NOT_PUBLISHED"

    assert publish_batch(api, [CODE]).status_code == 204
    assigned = take(api, a)
    assert assigned.status_code == 200
    assert take(api, b, assigned.json()["assignment_id"]).status_code == 202
    before = client_work(database, CODE)
    assert apply_snapshot(api, balances[::-1], batch_id=batch_id).status_code == 204
    conflict = apply_snapshot(api, [(CODE, 2), (other, 0)], batch_id=batch_id)
    assert conflict.status_code == 409 and conflict.json()["code"] == "SNAPSHOT_ID_CONFLICT"
    assert apply_snapshot(api, [(CODE, 2)]).status_code == 204
    assert apply_snapshot(api, [(CODE, 0)]).status_code == 204
    assert client_work(database, CODE) == before


def test_older_snapshot_does_not_change_current_quarter(api, operators, database):
    a, _, _ = operators
    current = "2026_Q4"
    assert apply_snapshot(api, [(CODE, 3)], quarter=current).status_code == 204
    assert publish_batch(api, [CODE], quarter=current).status_code == 204
    assigned = api.post(
        f"{CLIENT}/take",
        params={"work_quarter": current},
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    )
    assert assigned.status_code == 200
    before = client_work(database, CODE)
    assert apply_snapshot(api, [(CODE, 10)], quarter="2026_Q2").status_code == 204
    assert client_work(database, CODE) == before
    assert take(api, a).json()["code"] == "INVALID_STATE"
    current_state = api.get(
        f"{CLIENT}/assignment", params={"work_quarter": current}, headers=a
    ).json()
    assert current_state["work_quarter"] == current
    assert current_state["assignment_id"] == assigned.json()["assignment_id"]


def test_snapshot_rollover_carries_wait_into_unpublished_cycle(api, operators, database):
    a, b, _ = operators
    next_quarter = "2026_Q4"
    next_params = {"work_quarter": next_quarter}
    assert apply_snapshot(api, [(CODE, 8)]).status_code == 204
    assert publish_batch(api, [CODE]).status_code == 204
    assigned = take(api, a).json()
    call = call_payload(assigned)
    assert record_call(api, a, call).status_code == 201

    before = client_work(database, CODE)
    assert apply_snapshot(api, [(CODE, 0)], quarter=next_quarter).status_code == 204
    assert client_work(database, CODE) == before

    batch_id = uuid4()
    assert (
        apply_snapshot(api, [(CODE, 2)], quarter=next_quarter, batch_id=batch_id).status_code == 204
    )
    cycles, assignments, stages, events, _ = client_work(database, CODE)
    old_cycle, carried = cycles
    assert not old_cycle.is_current
    assert old_cycle._asdict() == {**before[0][0]._asdict(), "is_current": False}
    assert carried.work_quarter == next_quarter
    assert carried.source_import_batch_id == batch_id
    assert carried.published_at is None
    assert str(carried.wait_until) == call["wait_until"]
    assert carried.wait_source_work_quarter == QUARTER
    carried_assignment = next(row for row in assignments if row.work_quarter == next_quarter)
    assert str(carried_assignment.operator_id) == a["X-User-Id"]
    assert [row.stage for row in stages if row.work_quarter == next_quarter] == ["WAIT_DEADLINE"]
    assert events == before[3]
    assert api.get(f"{BASE}/pool", headers=b, params=next_params).json()["items"] == []
    assert api.get(f"{BASE}/pool", headers=b, params=CYCLE).json()["items"] == []

    assert publish_batch(api, [CODE], quarter=next_quarter).status_code == 204
    state = api.get(f"{CLIENT}/assignment", headers=b, params=next_params).json()
    assert state["assigned_operator_id"] == a["X-User-Id"]
    assert state["assignment_id"] == str(carried_assignment.id)
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["wait_until"] == call["wait_until"]
    attempt = api.post(
        f"{CLIENT}/take",
        params=next_params,
        headers=b,
        json={"expected_assignment_id": state["assignment_id"], "request_id": str(uuid4())},
    )
    assert attempt.json()["code"] == "CLIENT_WAIT_DEADLINE_ACTIVE"
    published = client_work(database, CODE)
    assert (
        apply_snapshot(api, [(CODE, 2)], quarter=next_quarter, batch_id=batch_id).status_code == 204
    )
    assert client_work(database, CODE) == published


def work_cycle_ids(api, admin, path=CLIENT):
    response = api.get(f"{path}/cycles", headers=admin)
    assert response.status_code == 200
    return {cycle["work_quarter"]: cycle for cycle in response.json()["items"]}


def test_quarter_cleanup_keeps_shared_events_and_other_work_cycles(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    publish(api)
    assert (
        record_call(
            api, a, call_payload(take(api, a).json(), document_quarters=["2025_Q1"])
        ).status_code
        == 201
    )
    shared = record_call(api, a, historical_comment()).json()
    assert apply_snapshot(api, [(CODE, 2)], quarter="2026_Q4").status_code == 204
    assert publish_batch(api, [CODE], quarter="2026_Q4").status_code == 204
    old_id = work_cycle_ids(api, admin)[QUARTER]["cycle_id"]
    before = client_work(database, CODE)
    old_events = [str(event.id) for event in before[3] if event.work_quarter == QUARTER]
    payload = {
        "scope": "QUARTER",
        "operation_id": str(uuid4()),
        "kcr_code": CODE,
        "quarter": "2025_Q1",
        "cycle_ids": [old_id],
        "event_ids": old_events,
    }

    partial = cleanup(api, admin, {**payload, "event_ids": old_events[1:]})
    assert partial.status_code == 409
    assert partial.json()["code"] == "CLEANUP_TARGET_CONFLICT"
    assert (
        cleanup(api, admin, {**payload, "event_ids": [*old_events, shared["id"]]}).status_code
        == 409
    )
    assert client_work(database, CODE) == before

    assert cleanup(api, admin, payload).status_code == 200
    cycles, assignments, stages, events, _ = client_work(database, CODE)
    assert cycles == [before[0][-1]]
    assert assignments == [row for row in before[1] if row.work_quarter == "2026_Q4"]
    assert stages == [row for row in before[2] if row.work_quarter == "2026_Q4"]
    assert [str(event.id) for event in events] == [shared["id"]]
    assert cleanup(api, admin, {**payload, "event_ids": old_events[::-1]}).status_code == 200
    assert (
        cleanup(api, admin, {**payload, "quarter": "2025_Q2"}).json()["code"]
        == "OPERATION_ID_CONFLICT"
    )


def test_batch_cleanup_restores_only_coordinator_selected_cycle(api, operators, database):
    a, b, _ = operators
    admin = identity("ADMIN")
    older = "2026_Q2"
    latest = "2026_Q4"
    latest_params = {"work_quarter": latest}
    publish(api, quarter=older)
    original = api.post(
        f"{CLIENT}/take",
        params={"work_quarter": older},
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    ).json()
    publish(api)
    batch_id = uuid4()
    assert apply_snapshot(api, [(CODE, 5)], quarter=latest, batch_id=batch_id).status_code == 204
    publish(api, quarter=latest)
    assert (
        api.post(
            f"{CLIENT}/take",
            params=latest_params,
            headers=a,
            json={"expected_assignment_id": None, "request_id": str(uuid4())},
        ).status_code
        == 200
    )
    cycle_ids = work_cycle_ids(api, admin)
    before = client_work(database, CODE)
    payload = {
        "scope": "BATCH",
        "operation_id": str(uuid4()),
        "kcr_code": CODE,
        "batch_id": str(batch_id),
        "cycle_ids": [cycle_ids[latest]["cycle_id"]],
        "event_ids": [str(event.id) for event in before[3] if event.work_quarter == latest],
    }
    assert cleanup(api, admin, payload).json()["code"] == "RESTORE_REQUIRED"
    assert (
        cleanup(api, admin, {**payload, "batch_id": str(uuid4())}).json()["code"]
        == "CLEANUP_TARGET_CONFLICT"
    )
    restore = {
        "cycle_id": cycle_ids[older]["cycle_id"],
        "stage": "WAIT_DEADLINE",
        "operator_id": b["X-User-Id"],
        "wait_until": "2036-12-22",
        "published": True,
    }
    wrong_restore = {**restore, "cycle_id": cycle_ids[latest]["cycle_id"]}
    assert (
        cleanup(api, admin, {**payload, "restore": wrong_restore}).json()["code"]
        == "INVALID_RESTORE"
    )
    assert client_work(database, CODE) == before

    request = {**payload, "restore": restore}
    assert cleanup(api, admin, request).status_code == 200
    after = work_cycle_ids(api, admin)
    assert set(after) == {older, QUARTER}
    assert after[older]["is_current"] and not after[QUARTER]["is_current"]
    state = api.get(f"{CLIENT}/assignment", params={"work_quarter": older}, headers=a).json()
    assert state["assigned_operator_id"] == b["X-User-Id"]
    assert state["assignment_id"] != original["assignment_id"]
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["wait_until"] == restore["wait_until"]
    assert api.get(f"{BASE}/pool", headers=a, params=CYCLE).json()["items"] == []
    assert api.get(f"{BASE}/pool", headers=a, params={"work_quarter": older}).json()["items"] == [
        {"kcr_code": CODE, "assigned_operator_id": b["X-User-Id"]},
    ]
    restored = client_work(database, CODE)
    assert cleanup(api, admin, request).status_code == 200
    assert apply_snapshot(api, [(CODE, 5)], quarter=latest, batch_id=batch_id).status_code == 204
    assert client_work(database, CODE) == restored


def test_quarter_cleanup_of_current_cycle_does_not_promote_history(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    publish(api)
    assert take(api, a).status_code == 200
    publish(api, quarter="2026_Q4")
    shared = record_call(api, a, historical_comment()).json()
    cycles = work_cycle_ids(api, admin)
    payload = {
        "scope": "QUARTER",
        "operation_id": str(uuid4()),
        "kcr_code": CODE,
        "quarter": "2026_Q2",
        "cycle_ids": [cycles["2026_Q4"]["cycle_id"]],
    }
    assert cleanup(api, admin, payload).status_code == 200
    assert not work_cycle_ids(api, admin)[QUARTER]["is_current"]
    assert api.get(f"{BASE}/pool", headers=a, params=CYCLE).json()["items"] == []
    assert publish_batch(api, [CODE]).status_code == 409
    history = api.get(f"{CLIENT}/events", headers=admin).json()["items"]
    assert shared["id"] in {event["id"] for event in history}


def test_snapshot_delivery_recovers_after_failure_and_restart(database):
    batch_id = uuid4()
    second = "AAA02210"
    balances = [(CODE, 3), (second, 2)]
    with database.begin() as db:
        db.execute(
            text(
                "ALTER TABLE client_work_cycles ADD CONSTRAINT simulated_delivery_failure "
                "CHECK (kcr_code <> 'AAA02210')"
            )
        )
    try:
        with (
            TestClient(create_app(database, "local-test")) as failed,
            pytest.raises(IntegrityError),
        ):
            apply_snapshot(failed, balances, batch_id=batch_id)
        assert client_work(database, CODE) == [[], [], [], [], []]
        with database.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM applied_work_snapshots")) == 0
    finally:
        with database.begin() as db:
            db.execute(
                text("ALTER TABLE client_work_cycles DROP CONSTRAINT simulated_delivery_failure")
            )

    with TestClient(create_app(database, "local-test")) as committed:
        assert apply_snapshot(committed, balances, batch_id=batch_id).status_code == 204
    saved = client_work(database, CODE)
    with TestClient(create_app(database, "local-test")) as restarted:
        assert apply_snapshot(restarted, balances[::-1], batch_id=batch_id).status_code == 204
        assert apply_snapshot(restarted, [(CODE, 9)], quarter="2026_Q1").status_code == 204
    assert client_work(database, CODE) == saved


def test_gateway_read_contract_and_trusted_actor(api, operators):
    a, _, _ = operators
    spoofed = {key: value for key, value in a.items() if key != "Authorization"}
    assert api.get(f"{BASE}/pool", headers=spoofed, params=CYCLE).status_code == 401
    assert api.get(f"{BASE}/pool", params=CYCLE).status_code == 401
    assert (
        api.get(f"{BASE}/pool", headers={**a, "X-User-Id": "invalid"}, params=CYCLE).status_code
        == 422
    )
    assert api.get(f"{CLIENT}/cycles", headers=a).status_code == 403

    publish(api)
    assignment = take(api, a).json()
    assert record_call(api, a, call_payload(assignment)).status_code == 201
    state = api.get(f"{CLIENT}/assignment", headers=a, params=CYCLE).json()
    assert state["assigned_operator_id"] == a["X-User-Id"]
    assert state["stage"] == "WAIT_DEADLINE"
    assert state["wait_until"] == "2036-11-21"
    history = api.get(f"{CLIENT}/events", headers=a, params={"page_size": 1}).json()
    assert history["items"][0]["author_id"] == a["X-User-Id"]
    assert history["page_size"] == 1 and history["total"] == 3
    contract = api.get("/openapi.json").json()
    for path, operation, schema in (
        ("/pool", "listPublishedClients", "PoolResponse"),
        ("/clients/{kcr_code}/assignment", "readAssignment", "AssignmentState"),
        ("/clients/{kcr_code}/events", "listClientEvents", "EventListResponse"),
    ):
        entry = contract["paths"][BASE + path]["get"]
        assert entry["operationId"] == operation
        assert entry["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/" + schema
        )


def test_event_only_cleanup_does_not_change_surviving_cycle(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    publish(api)
    assigned = take(api, a).json()
    assert record_call(api, a, call_payload(assigned)).status_code == 201
    selected = record_call(api, a, historical_comment(document_quarters=["2025_Q4"])).json()
    common = record_call(api, a, historical_comment()).json()
    before = client_work(database, CODE)
    payload = {
        "scope": "QUARTER",
        "operation_id": str(uuid4()),
        "kcr_code": CODE,
        "quarter": "2025_Q4",
        "event_ids": [selected["id"]],
    }
    assert cleanup(api, admin, payload).status_code == 200
    after = client_work(database, CODE)
    assert after[:3] == before[:3] and after[4] == before[4]
    assert after[3] == [event for event in before[3] if str(event.id) != selected["id"]]
    assert common["id"] in {str(event.id) for event in after[3]}


def test_coordinator_reads_restore_state_from_historical_cycle(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    origin = "2026_Q2"
    newest = "2026_Q4"
    publish(api, quarter=origin)
    first = api.post(
        f"{CLIENT}/take",
        params={"work_quarter": origin},
        headers=a,
        json={"expected_assignment_id": None, "request_id": str(uuid4())},
    ).json()
    assert record_call(api, a, call_payload(first, work_quarter=origin)).status_code == 201
    assert apply_snapshot(api, [(CODE, 6)]).status_code == 204
    publish(api)
    batch_id = uuid4()
    assert apply_snapshot(api, [(CODE, 2)], quarter=newest, batch_id=batch_id).status_code == 204

    before_read = client_work(database, CODE)
    cycles = work_cycle_ids(api, admin)
    assert client_work(database, CODE) == before_read
    assert api.get(f"{CLIENT}/cycles", headers=a).status_code == 403
    historical = cycles[QUARTER]
    assert not historical["is_current"] and historical["published_at"] is not None
    assert cycles[newest]["is_current"] and cycles[newest]["published_at"] is None
    assert historical["assigned_operator_id"] == a["X-User-Id"]
    assert historical["stage"] == "WAIT_DEADLINE"
    assert historical["wait_until"] == "2036-11-21"
    assert historical["wait_source_work_quarter"] == origin
    assert cycles[newest]["wait_source_work_quarter"] == QUARTER

    response = cleanup(
        api,
        admin,
        {
            "scope": "BATCH",
            "operation_id": str(uuid4()),
            "kcr_code": CODE,
            "batch_id": str(batch_id),
            "cycle_ids": [cycles[newest]["cycle_id"]],
            "restore": {
                "cycle_id": historical["cycle_id"],
                "published": historical["published_at"] is not None,
                "stage": historical["stage"],
                "operator_id": historical["assigned_operator_id"],
                "wait_until": historical["wait_until"],
                "wait_source_work_quarter": historical["wait_source_work_quarter"],
            },
        },
    )
    assert response.status_code == 200
    assert work_cycle_ids(api, admin)[QUARTER] == {**historical, "is_current": True}


def test_explicit_quarter_links_protect_shared_and_other_period_events(api, operators, database):
    a, _, _ = operators
    admin = identity("ADMIN")
    publish(api)
    assigned = take(api, a).json()
    payload = call_payload(assigned, document_quarters=["2025_Q4", "2025_Q2"])
    shared = record_call(api, a, payload)
    assert shared.status_code == 201
    assert shared.json()["document_quarters"] == ["2025_Q2", "2025_Q4"]
    replay = record_call(api, a, {**payload, "document_quarters": ["2025_Q2", "2025_Q4"]})
    assert replay.status_code == 200 and replay.json() == shared.json()
    assert record_call(api, a, {**payload, "document_quarters": ["2025_Q2"]}).status_code == 409
    single = record_call(api, a, historical_comment(document_quarters=["2025_Q3"])).json()
    assert record_call(api, a, historical_comment(document_quarters=["2025"])).status_code == 422
    assert (
        record_call(
            api, a, historical_comment(document_quarters=["2025_Q3", "2025_Q3"])
        ).status_code
        == 422
    )
    assert single["work_quarter"] is None
    events = api.get(f"{CLIENT}/events", headers=admin).json()["items"]
    links = {event["id"]: event["document_quarters"] for event in events}
    assert links[shared.json()["id"]] == ["2025_Q2", "2025_Q4"]
    assert links[single["id"]] == ["2025_Q3"]

    before = client_work(database, CODE)
    cleanup_request = {
        "scope": "QUARTER",
        "operation_id": str(uuid4()),
        "kcr_code": CODE,
        "quarter": "2025_Q2",
        "event_ids": [shared.json()["id"]],
    }
    assert cleanup(api, admin, cleanup_request).status_code == 409
    assert cleanup(api, admin, {**cleanup_request, "event_ids": [single["id"]]}).status_code == 409
    assert client_work(database, CODE) == before


def snapshot_part(number, *, total=2):
    return {"delivery_id": str(uuid4()), "part_number": number, "part_count": total}


def test_large_snapshot_resumes_a_failed_part_and_keeps_original_batch(api, database):
    batch_id = uuid4()
    first, second = snapshot_part(1), snapshot_part(2)
    large = [(f"TEST{i:04d}", 1) for i in range(1000)]
    remainder = [(CODE, 3), ("ZZFAIL", 2)]
    assert apply_snapshot(api, large, batch_id=batch_id, delivery=first).status_code == 204
    with database.begin() as db:
        db.execute(
            text(
                "ALTER TABLE client_work_cycles ADD CONSTRAINT simulated_part_failure "
                "CHECK (kcr_code <> 'ZZFAIL')"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            apply_snapshot(api, remainder, batch_id=batch_id, delivery=second)
        assert client_work(database, CODE) == [[], [], [], [], []]
        with database.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM applied_work_snapshot_parts")) == 1
            assert db.scalar(text("SELECT count(*) FROM client_work_cycles")) == 1000
    finally:
        with database.begin() as db:
            db.execute(
                text("ALTER TABLE client_work_cycles DROP CONSTRAINT simulated_part_failure")
            )

    with TestClient(create_app(database, "local-test")) as restarted:
        assert (
            apply_snapshot(restarted, large[::-1], batch_id=batch_id, delivery=first).status_code
            == 204
        )
        assert (
            apply_snapshot(restarted, remainder, batch_id=batch_id, delivery=second).status_code
            == 204
        )
    assert apply_snapshot(api, remainder, batch_id=batch_id, delivery=second).status_code == 204
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM applied_work_snapshot_parts")) == 2
        assert db.scalar(text("SELECT count(*) FROM client_work_cycles")) == 1002
        assert (
            db.scalar(
                text("SELECT count(*) FROM client_work_cycles WHERE source_import_batch_id = :id"),
                {"id": batch_id},
            )
            == 1002
        )
        assert (
            db.scalar(
                text("SELECT count(*) FROM client_work_cycles WHERE published_at IS NOT NULL")
            )
            == 0
        )


def test_snapshot_parts_reject_conflicting_deliveries(api, database):
    batch_id = uuid4()
    first, second = snapshot_part(1), snapshot_part(2)
    assert apply_snapshot(api, [(CODE, 2)], batch_id=batch_id, delivery=first).status_code == 204
    cases = [
        ([(CODE, 9)], first, QUARTER, "SNAPSHOT_PART_CONFLICT"),
        ([(CODE, 2)], second, QUARTER, "SNAPSHOT_PART_CONFLICT"),
        ([("AAA02210", 2)], snapshot_part(1), QUARTER, "SNAPSHOT_PART_CONFLICT"),
        ([("AAA02210", 2)], second, "2026_Q4", "SNAPSHOT_ID_CONFLICT"),
        ([("AAA02210", 2)], {**second, "part_count": 3}, QUARTER, "SNAPSHOT_ID_CONFLICT"),
        ([("AAA02210", 2)], None, QUARTER, "SNAPSHOT_ID_CONFLICT"),
    ]
    for balances, delivery, quarter, error in cases:
        response = apply_snapshot(
            api, balances, batch_id=batch_id, delivery=delivery, quarter=quarter
        )
        assert response.status_code == 409 and response.json()["code"] == error
    invalid = {**second, "part_number": 3}
    assert (
        apply_snapshot(api, [("AAA02210", 2)], batch_id=batch_id, delivery=invalid).status_code
        == 422
    )
    with database.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM applied_work_snapshot_parts")) == 1
    assert (
        apply_snapshot(api, [("AAA02210", 2)], batch_id=batch_id, delivery=second).status_code
        == 204
    )
    assert apply_snapshot(api, [(CODE, 2)], batch_id=batch_id, delivery=first).status_code == 204
    old_id = uuid4()
    assert apply_snapshot(api, [(CODE, 2)], batch_id=old_id).status_code == 204
    assert apply_snapshot(api, [(CODE, 2)], batch_id=old_id, delivery=first).status_code == 409
