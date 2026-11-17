import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from import_worker import api
from import_worker.config import Settings
from import_worker.consumer import claim_apply
from import_worker.database import ImportBatch
from import_worker.operator_work import AffectedClient
from import_worker.rollback import RollbackProblem, cleanup_command


def cycle(batch_id, **fields):
    return {
        "cycle_id": str(uuid4()),
        "work_quarter": "2026_Q2",
        "is_current": True,
        "published_at": None,
        "source_import_batch_id": str(batch_id),
        "assigned_operator_id": None,
        "stage": "POOL",
        "wait_until": None,
        "wait_source_work_quarter": None,
        **fields,
    }


async def test_shared_history_blocks_cycle_removal_during_planning() -> None:
    batch_id = uuid4()
    current = cycle(batch_id)
    shared = {
        "id": str(uuid4()),
        "work_quarter": "2026_Q2",
        "event_type": "CALL_RESULT",
        "document_quarters": ["2026_Q1", "2026_Q2"],
    }

    def respond(request):
        assert request.method == "GET"
        if request.url.path.endswith("/cycles"):
            return httpx.Response(200, json={"items": [current]})
        return httpx.Response(200, json={"total": 1, "items": [shared]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://operator-work"
    ) as work:
        affected = AffectedClient(
            kcr_code="A", source_quarters=["2026_Q2"], outstanding_documents=2
        )
        with pytest.raises(RollbackProblem) as failure:
            await cleanup_command(work, affected, batch_id, uuid4())
        assert failure.value.code == "CLEANUP_TARGET_CONFLICT"


async def test_rollback_resumes_after_lost_cleanup_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = os.getenv("IMPORT_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("Укажите Import DB с применёнными миграциями")
    batch_id, actor, key, operator = uuid4(), uuid4(), uuid4(), uuid4()
    prior = cycle(
        uuid4(),
        work_quarter="2026_Q1",
        is_current=False,
        published_at="2026-03-01T12:00:00Z",
        stage="WAIT_DEADLINE",
        assigned_operator_id=str(operator),
        wait_until="2036-04-20",
    )
    removed = {"A": cycle(batch_id, wait_source_work_quarter="2026_Q1"), "B": cycle(batch_id)}
    event = {
        "id": str(uuid4()),
        "work_quarter": "2026_Q2",
        "event_type": "STAGE_CHANGE",
        "document_quarters": [],
    }
    cleanup_calls = []
    clients_steps = []
    client_type = httpx.AsyncClient

    def respond(request):
        path = request.url.path
        if request.url.host == "clients":
            clients_steps.append(path.rsplit("/", 1)[-1])
            if path.endswith("/affected-clients"):
                return httpx.Response(
                    200,
                    json={
                        "batch_id": str(batch_id),
                        "limit": 1000,
                        "offset": 0,
                        "total": 2,
                        "items": [
                            {
                                "kcr_code": code,
                                "source_quarters": ["2026_Q2"],
                                "outstanding_documents": 7,
                            }
                            for code in removed
                        ],
                    },
                )
            if path.endswith("/rollback"):
                assert json.loads(request.content) == {
                    "requested_by": str(actor),
                    "operation_id": str(key),
                }
                return httpx.Response(
                    200,
                    json={
                        "batch_id": str(batch_id),
                        "operation_id": str(key),
                        "local_only": True,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "batch_id": str(batch_id),
                    "rollback_confirmed_at": "2026-11-17T12:00:00Z",
                },
            )
        assert request.headers["x-user-id"] == str(actor)
        assert request.headers["x-user-role"] == "ADMIN"
        if path.endswith("/cycles"):
            code = path.split("/")[-2]
            cycles = [removed[code], prior] if code == "A" else [removed[code]]
            return httpx.Response(200, json={"items": cycles})
        if path.endswith("/events"):
            assert request.url.params["work_quarter"] == "2026_Q2"
            return httpx.Response(200, json={"total": 1, "items": [event]})
        command = json.loads(request.content)
        cleanup_calls.append(command)
        if len(cleanup_calls) == 2:
            raise httpx.ReadTimeout("Подтверждение очистки потеряно")
        return httpx.Response(200, json={**command, "status": "COMPLETED"})

    monkeypatch.setattr(
        api.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw),
    )
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine)
    app = api.create_app(
        Settings(
            db_url,
            "amqp://unused",
            tmp_path,
            clients_url="http://clients",
            operator_work_url="http://operator-work",
        )
    )
    try:
        async with sessions.begin() as db:
            db.add(
                ImportBatch(
                    id=batch_id,
                    uploaded_by=uuid4(),
                    import_type="SNAPSHOT",
                    selected_quarter="2026_Q2",
                    snapshot_date=date(2026, 4, 20),
                    filename="snapshot.xlsx",
                    status="COMPLETED",
                    clients_applied_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
            )
        headers = {"X-User-Id": str(actor)}
        path = f"/internal/v1/imports/{batch_id}"
        async with client_type(
            transport=httpx.ASGITransport(app=app), base_url="http://import"
        ) as client:
            failed = await client.post(
                f"{path}/rollback", headers=headers, json={"operation_id": str(key)}
            )
            assert failed.status_code == 503, failed.text
            assert (await client.get(path)).json()["status"] == "COMPLETED"
            assert clients_steps == ["affected-clients", "rollback"]
            with pytest.raises(RuntimeError, match="отката"):
                await claim_apply(sessions, uuid4())
            conflict = await client.post(
                f"{path}/rollback", headers=headers, json={"operation_id": str(uuid4())}
            )
            assert (
                conflict.status_code == 409 and conflict.json()["code"] == "OPERATION_ID_CONFLICT"
            )
            success = await client.post(
                f"{path}/rollback", headers=headers, json={"operation_id": str(key)}
            )
            assert success.status_code == 200, success.text
            assert success.json()["status"] == "ROLLED_BACK"
            replay = await client.post(
                f"{path}/rollback", headers=headers, json={"operation_id": str(key)}
            )
            assert replay.json() == success.json()
        assert clients_steps == ["affected-clients", "rollback", "rollback-confirmation"]
        assert [command["kcr_code"] for command in cleanup_calls] == ["A", "B", "B"]
        assert cleanup_calls[1] == cleanup_calls[2]
        assert cleanup_calls[0]["cycle_ids"] == [removed["A"]["cycle_id"]]
        assert cleanup_calls[0]["event_ids"] == [event["id"]]
        assert cleanup_calls[0]["restore"] == {
            "cycle_id": prior["cycle_id"],
            "published": True,
            "stage": "WAIT_DEADLINE",
            "operator_id": str(operator),
            "wait_until": "2036-04-20",
            "wait_source_work_quarter": None,
        }
    finally:
        await engine.dispose()
