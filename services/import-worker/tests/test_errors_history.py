import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from import_worker import errors_history
from import_worker.database import ImportBatch, ImportError, ImportErrorDelivery, add_errors
from import_worker.errors_history import deliver_one
from import_worker.models import ImportErrorRow


@pytest.fixture
async def empty_database(monkeypatch):
    url = os.getenv("IMPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Укажите IMPORT_TEST_DATABASE_URL")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    name = f"import_outbox_test_{uuid4().hex}"
    try:
        async with admin.connect() as connection:
            await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        test_url = make_url(url).set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("IMPORT_DATABASE_URL", test_url)
        engine = create_async_engine(test_url)
        try:
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            yield async_sessionmaker(engine), config
        finally:
            await engine.dispose()
            async with admin.connect() as connection:
                await connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        await admin.dispose()


@pytest.fixture
async def outbox_sessions(empty_database):
    sessions, config = empty_database
    await asyncio.to_thread(command.upgrade, config, "head")
    return sessions


def workbook_error(reason: str) -> ImportErrorRow:
    return ImportErrorRow(sheet="", row_number=0, kcr_code=None, reason=reason)


async def registry(db) -> ImportBatch:
    batch = ImportBatch(
        id=uuid4(), uploaded_by=uuid4(), filename="test.xlsx", import_type="REGISTRY"
    )
    db.add(batch)
    await db.flush()
    return batch


async def enqueue(sessions, *reasons: str) -> list[ImportErrorDelivery]:
    async with sessions.begin() as db:
        await add_errors(db, await registry(db), [workbook_error(reason) for reason in reasons])
    async with sessions() as db:
        return list(
            await db.scalars(
                select(ImportErrorDelivery).order_by(ImportErrorDelivery.source_error_id)
            )
        )


async def make_due(sessions, source_id: int) -> None:
    async with sessions.begin() as db:
        await db.execute(
            update(ImportErrorDelivery)
            .where(ImportErrorDelivery.source_error_id == source_id)
            .values(next_attempt_at=func.now())
        )


@pytest.mark.parametrize(
    ("status", "field"),
    [
        (200, "delivered_at"),
        (409, "rejected_status"),
        (503, "next_attempt_at"),
        (None, "next_attempt_at"),
    ],
)
async def test_delivery_finishes_claim_before_http(db, sessions, status, field) -> None:
    delivery = ImportErrorDelivery(source_error_id=42, payload='{"errors":[]}', attempt_count=0)
    db.scalar.return_value = delivery

    def reply(request: httpx.Request) -> httpx.Response:
        sessions.begin.return_value.__aexit__.assert_awaited_once()
        assert delivery.attempt_count == 1
        assert request.content == delivery.payload.encode()
        if status is None:
            raise httpx.ConnectError("unavailable", request=request)
        return httpx.Response(status, json={"inserted": 0, "existing": 1})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), base_url="http://errors-history"
    ) as history:
        assert await deliver_one(sessions, history)
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0].compile()
    parameters = statement.params
    assert f" SET {field}=" in str(statement)
    assert parameters["source_error_id_1"] == 42 and parameters["attempt_count_1"] == 1


async def test_background_sender_resumes_after_database_error(monkeypatch, sessions) -> None:
    deliver = AsyncMock(side_effect=[SQLAlchemyError(), True, asyncio.CancelledError()])
    monkeypatch.setattr(errors_history, "deliver_one", deliver)
    monkeypatch.setattr(errors_history, "POLL_INTERVAL", 0)
    async with httpx.AsyncClient() as history:
        with pytest.raises(asyncio.CancelledError):
            await errors_history.deliver_errors(sessions, history)
    assert deliver.await_count == 3


async def test_upgrade_preserves_old_errors_without_timestamp(empty_database) -> None:
    sessions, config = empty_database
    await asyncio.to_thread(command.upgrade, config, "0005_remove_client_blocking")
    async with sessions.begin() as db:
        batch = await registry(db)
        legacy_id = await db.scalar(
            text("""
                INSERT INTO import_errors (batch_id, sheet, row_number, reason)
                VALUES (:batch, '', 0, 'Old failure') RETURNING id
            """),
            {"batch": batch.id},
        )
        batch_id = batch.id

    await asyncio.to_thread(command.upgrade, config, "head")
    async with sessions.begin() as db:
        legacy = await db.get(ImportError, legacy_id)
        assert legacy.recorded_at is None and legacy.kcr_code is None
        assert await db.get(ImportErrorDelivery, legacy_id) is None
        await add_errors(db, await db.get(ImportBatch, batch_id), [workbook_error("New failure")])
    async with sessions() as db:
        delivery = await db.scalar(select(ImportErrorDelivery))
        error = await db.get(ImportError, delivery.source_error_id)
        assert error.recorded_at is not None
        assert json.loads(delivery.payload)["errors"][0] == {
            "source_error_id": error.id,
            "batch_id": str(batch_id),
            "import_type": "REGISTRY",
            "sheet": "",
            "row_number": 0,
            "kcr_code": None,
            "reason": "New failure",
            "recorded_at": error.recorded_at.isoformat(),
        }


async def test_sender_only_sees_committed_errors(outbox_sessions) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Uncommitted error sent to {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected), base_url="http://errors-history"
    ) as history:
        with pytest.raises(RuntimeError, match="rollback"):
            async with outbox_sessions.begin() as db:
                await add_errors(db, await registry(db), [workbook_error("Aborted")])
                await db.flush()
                assert not await deliver_one(outbox_sessions, history)
                raise RuntimeError("rollback")
        assert not await deliver_one(outbox_sessions, history)
    async with outbox_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ImportError)) == 0
        assert await db.scalar(select(func.count()).select_from(ImportErrorDelivery)) == 0


async def test_retries_identical_body_and_keeps_conflict(outbox_sessions) -> None:
    retry, conflict, normal = await enqueue(outbox_sessions, "retry", "conflict", "normal")
    requests: list[bytes] = []
    failures = 0

    def reply(request: httpx.Request) -> httpx.Response:
        nonlocal failures
        assert request.url.path == "/internal/v1/import-errors/bulk"
        assert request.headers["content-type"] == "application/json"
        requests.append(request.content)
        (event,) = json.loads(request.content)["errors"]
        match event["reason"]:
            case "retry":
                failures += 1
                if failures == 1:
                    raise httpx.ConnectError("unavailable", request=request)
                if failures == 2:
                    return httpx.Response(503)
                return httpx.Response(200, json={"inserted": 0, "existing": 1})
            case "conflict":
                return httpx.Response(409)
            case _:
                return httpx.Response(200, json={"inserted": 1, "existing": 0})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), base_url="http://errors-history"
    ) as history:
        for _ in range(3):
            assert await deliver_one(outbox_sessions, history)

        async with outbox_sessions() as db:
            pending = await db.get(ImportErrorDelivery, retry.source_error_id)
            assert pending.delivered_at is None and pending.attempt_count == 1
            assert pending.next_attempt_at > await db.scalar(select(func.now()))
        await make_due(outbox_sessions, retry.source_error_id)
        assert await deliver_one(outbox_sessions, history)
        await make_due(outbox_sessions, retry.source_error_id)
        assert await deliver_one(outbox_sessions, history)
        assert not await deliver_one(outbox_sessions, history)

    assert requests == [item.payload.encode() for item in (retry, conflict, normal, retry, retry)]
    async with outbox_sessions() as db:
        done = await db.get(ImportErrorDelivery, retry.source_error_id)
        assert done.delivered_at is not None and done.attempt_count == 3
        rejected = await db.get(ImportErrorDelivery, conflict.source_error_id)
        assert rejected.delivered_at is None and rejected.rejected_status == 409
        assert rejected.payload == conflict.payload and rejected.attempt_count == 1
        assert (await db.get(ImportErrorDelivery, normal.source_error_id)).delivered_at is not None


async def test_expired_reservation_can_retry_without_late_write(outbox_sessions) -> None:
    (pending,) = await enqueue(outbox_sessions, "reserved")
    sending = asyncio.Event()
    finish_first = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        assert request.content == pending.payload.encode()
        sending.set()
        await finish_first.wait()
        return httpx.Response(409)

    async def fast(request: httpx.Request) -> httpx.Response:
        assert request.content == pending.payload.encode()
        return httpx.Response(200, json={"inserted": 0, "existing": 1})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="http://slow") as first,
        httpx.AsyncClient(transport=httpx.MockTransport(fast), base_url="http://fast") as second,
        asyncio.TaskGroup() as tasks,
    ):
        attempt = tasks.create_task(deliver_one(outbox_sessions, first))
        await asyncio.wait_for(sending.wait(), timeout=3)
        assert not await deliver_one(outbox_sessions, second)
        await make_due(outbox_sessions, pending.source_error_id)
        assert await deliver_one(outbox_sessions, second)
        finish_first.set()
    assert attempt.result()
    async with outbox_sessions() as db:
        saved = await db.get(ImportErrorDelivery, pending.source_error_id)
        assert saved.attempt_count == 2 and saved.rejected_status is None
        assert saved.delivered_at is not None
