import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aio_pika
import httpx
import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel
from conftest import REGISTRY_HEADER, WorkbookFactory
from pamqp.commands import Basic

from import_worker import broker, consumer, operator_work
from import_worker.broker import Job
from import_worker.clients import fingerprint
from import_worker.config import Settings
from import_worker.database import ImportBatch, ImportRollback, OperatorDelivery
from import_worker.models import ImportParameters, ParsedImport, Status


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings("postgresql+asyncpg://unused", "amqp://unused", tmp_path)


@pytest.fixture
def batch(db: AsyncMock, settings: Settings, xlsx: WorkbookFactory) -> ImportBatch:
    content = xlsx({"Срез": [["001", None, None, 8, 2], ["002", None, None, 5, 0]]})
    batch = ImportBatch(
        id=uuid4(),
        uploaded_by=uuid4(),
        selected_quarter="2026_Q2",
        import_type="SNAPSHOT",
        snapshot_date=date(2026, 4, 10),
        status=Status.PROCESSING,
        success_count=0,
        error_count=0,
        file_sha256=hashlib.sha256(content).hexdigest(),
    )
    (settings.upload_dir / f"{batch.id}.xlsx").write_bytes(content)
    db.get.return_value = batch
    db.scalar.return_value = None
    db.scalars.return_value = []
    return batch


def delivery(batch: ImportBatch) -> AsyncMock:
    return AsyncMock(
        spec=AbstractIncomingMessage, body=Job(batch_id=batch.id).model_dump_json().encode()
    )


async def test_retries_same_batch_after_lost_clients_response(
    settings: Settings,
    batch: ImportBatch,
    db: AsyncMock,
    sessions: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    xlsx: WorkbookFactory,
) -> None:
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    registry = xlsx(
        {
            "Реестр": [
                REGISTRY_HEADER,
                ["001", "Организация 1", None, None, None, None, None, "VIP"],
                ["002", "Организация 2", None, None, None, None, None, "Новичок"],
            ]
        }
    )
    batch.import_type = "REGISTRY"
    batch.selected_quarter = batch.snapshot_date = None
    batch.file_sha256 = hashlib.sha256(registry).hexdigest()
    (settings.upload_dir / f"{batch.id}.xlsx").write_bytes(registry)
    db.scalar.side_effect = [None, None, None, batch.id]
    requests = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            raise httpx.ReadTimeout("Clients Service did not respond")
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch.id),
                "accepted_rows": 1,
                "rejected_rows": 1,
                "errors": [
                    {"row_number": 2, "code": "UNKNOWN_KCR_CODE", "field": "client.kcr_code"}
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), base_url="http://clients"
    ) as clients:
        first = delivery(batch)
        await consumer.process_delivery(first, sessions, settings, clients)
        first.reject.assert_awaited_once_with(requeue=True)
        first.ack.assert_not_awaited()
        assert batch.apply_payload and batch.apply_started_at is not None
        assert batch.clients_applied_at is None
        assert list(settings.upload_dir.iterdir())

        replay = delivery(batch)
        await consumer.process_delivery(replay, sessions, settings, clients)
        replay.ack.assert_awaited_once()

    assert requests[0] == requests[1]
    assert requests[0]["batch_id"] == str(batch.id)
    assert {"selected_quarter", "snapshot_date"}.isdisjoint(requests[0])
    assert [item["client"]["kcr_code"] for item in requests[0]["items"]] == ["001", "002"]
    assert (batch.total_rows, batch.success_count, batch.error_count) == (2, 1, 1)
    error = db.add_all.call_args.args[0][0]
    assert (error.sheet, error.row_number, error.kcr_code) == ("Реестр", 3, "002")
    db.scalars.assert_not_awaited()
    assert batch.apply_payload is None and batch.clients_applied_at is not None
    assert batch.status == Status.COMPLETED and batch.completed_at is not None
    assert not list(settings.upload_dir.iterdir())


@pytest.mark.parametrize("clients_removed", [False, True])
async def test_duplicate_observations_check_current_registry(
    clients_removed: bool,
    settings: Settings,
    batch: ImportBatch,
    db: AsyncMock,
    sessions: MagicMock,
) -> None:
    path = settings.upload_dir / f"{batch.id}.xlsx"
    assert await consumer.prepare(sessions, batch.id, path)
    params = ImportParameters.model_validate(batch, from_attributes=True)
    parsed = ParsedImport.model_validate(batch.apply_payload)
    db.scalars.return_value = [fingerprint(params, row) for row in parsed.rows]
    errors = (
        [
            {"row_number": index, "code": "UNKNOWN_KCR_CODE", "field": "client.kcr_code"}
            for index in (1, 2)
        ]
        if clients_removed
        else []
    )

    def reply(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        assert [(item["client"]["kcr_code"], item["verify_only"]) for item in items] == [
            ("001", True),
            ("002", True),
        ]
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch.id),
                "accepted_rows": 0,
                "verified_rows": len(items) - len(errors),
                "rejected_rows": len(errors),
                "errors": errors,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), base_url="http://clients"
    ) as clients:
        message = delivery(batch)
        await consumer.process_delivery(message, sessions, settings, clients)
    message.ack.assert_awaited_once()
    assert batch.error_count == len(errors)
    assert batch.success_count == 2 - len(errors)
    assert batch.status == (Status.FAILED if clients_removed else Status.COMPLETED)
    if clients_removed:
        logged = db.add_all.call_args.args[0]
        assert [(row.sheet, row.row_number, row.kcr_code) for row in logged] == [
            ("Срез", 1, "001"),
            ("Срез", 2, "002"),
        ]
        assert all("Код КЦР отсутствует в реестре" in row.reason for row in logged)
    assert batch.clients_applied_at is None
    assert batch.apply_payload is None and not path.exists()


@pytest.mark.parametrize("status_code", [422, 200])
async def test_rejected_or_inconsistent_clients_report(
    status_code: int,
    settings: Settings,
    batch: ImportBatch,
    sessions: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    result = {
        "batch_id": str(batch.id),
        "accepted_rows": 2,
        "rejected_rows": 1,
        "errors": [{"row_number": 2, "code": "UNKNOWN_KCR_CODE"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code, json=result)),
        base_url="http://clients",
    ) as clients:
        message = delivery(batch)
        await consumer.process_delivery(message, sessions, settings, clients)
    if status_code == 422:
        message.ack.assert_awaited_once()
        assert batch.status == Status.FAILED
        assert batch.error_count == 1 and batch.apply_payload is None
    else:
        message.reject.assert_awaited_once_with(requeue=True)
        assert batch.status == Status.PROCESSING and batch.apply_payload is not None
    assert batch.clients_applied_at is None


async def test_changed_upload_fails_before_calling_clients(
    settings: Settings, batch: ImportBatch, db: AsyncMock, sessions: MagicMock
) -> None:
    path = settings.upload_dir / f"{batch.id}.xlsx"
    path.write_bytes(b"corrupted")
    clients = AsyncMock(spec=httpx.AsyncClient)
    message = delivery(batch)
    await consumer.process_delivery(message, sessions, settings, clients)
    message.ack.assert_awaited_once()
    clients.post.assert_not_awaited()
    assert batch.status == Status.FAILED and batch.error_count == 1
    assert db.add_all.call_args.args[0][0].row_number == 0
    assert not path.exists()


async def test_publish_marks_batch_only_after_broker_ack(
    batch: ImportBatch, db: AsyncMock, sessions: MagicMock
) -> None:
    publish = AsyncMock(side_effect=[Basic.Nack(delivery_tag=1), Basic.Ack(delivery_tag=2)])
    channel = MagicMock(spec=AbstractRobustChannel, default_exchange=MagicMock(publish=publish))
    db.scalar.return_value = batch

    with pytest.raises(RuntimeError, match="RabbitMQ"):
        await broker.publish_one(sessions, channel)
    assert batch.published_at is None
    assert await broker.publish_one(sessions, channel)
    assert batch.published_at is not None
    message = publish.await_args.args[0]
    assert Job.model_validate_json(message.body).batch_id == batch.id
    assert message.delivery_mode is aio_pika.DeliveryMode.PERSISTENT
    assert publish.await_args.kwargs["mandatory"] is True

    db.scalar.return_value = None
    assert not await broker.publish_one(sessions, channel)
    assert publish.await_count == 2


@pytest.mark.parametrize("publisher", [True, False])
async def test_broker_loops_reconnect_and_allow_cancellation(
    publisher: bool,
    settings: Settings,
    sessions: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = AsyncMock()
    channel = connection.channel.return_value
    queue = channel.declare_queue.return_value
    connect = AsyncMock(side_effect=[ConnectionError, connection, asyncio.CancelledError])
    monkeypatch.setattr(aio_pika, "connect_robust", connect)
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    monkeypatch.setattr(broker, "RETRY_DELAY", 0)
    if publisher:
        monkeypatch.setattr(broker, "publish_one", AsyncMock(side_effect=ConnectionError))
        operation = broker.publish_pending(settings.amqp_url, sessions)
    else:
        queue.iterator = MagicMock(side_effect=ConnectionError)
        operation = consumer.consume(settings, sessions, None)

    with pytest.raises(asyncio.CancelledError):
        await operation
    assert connect.await_count == 3
    channel.declare_queue.assert_awaited_once_with(
        broker.QUEUE_NAME, durable=True, arguments=broker.QUEUE_ARGUMENTS
    )


async def test_snapshot_resumes_only_the_unconfirmed_part_after_throttling(
    settings: Settings,
    batch: ImportBatch,
    db: AsyncMock,
    sessions: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operator_work, "PART_SIZE", 1)
    pause = AsyncMock()
    monkeypatch.setattr(consumer.asyncio, "sleep", pause)
    parts = []
    calls = []

    def persist(rows):
        for row in rows:
            if isinstance(row, OperatorDelivery):
                row.retry_count = 0
                parts.append(row)

    db.add_all.side_effect = persist

    async def scalar(query):
        entity = query.column_descriptions[0]["entity"]
        if entity is ImportRollback:
            return None
        if entity is ImportBatch:
            return batch.id if batch.apply_started_at is not None else None
        assert entity is OperatorDelivery
        return next((part for part in parts if part.confirmed_at is None), None)

    db.scalar.side_effect = scalar
    balances = [
        {"kcr_code": "001", "outstanding_documents": 2},
        {"kcr_code": "002", "outstanding_documents": 0},
    ]

    def clients_reply(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/internal/v1/import/apply"
            return httpx.Response(
                200,
                json={
                    "batch_id": str(batch.id),
                    "accepted_rows": 2,
                    "rejected_rows": 0,
                    "errors": [],
                },
            )
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch.id),
                "limit": 1,
                "offset": offset,
                "total": 2,
                "items": balances[offset : offset + 1],
            },
        )

    def work_reply(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "45"})
        return httpx.Response(204)

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(clients_reply), base_url="http://clients"
        ) as clients,
        httpx.AsyncClient(
            transport=httpx.MockTransport(work_reply), base_url="http://work"
        ) as work,
    ):
        first = delivery(batch)
        await consumer.process_delivery(first, sessions, settings, clients, work)
        first.reject.assert_awaited_once_with(requeue=True)
        first.ack.assert_not_awaited()
        pause.assert_awaited_once_with(45)
        assert batch.status == Status.PROCESSING and batch.completed_at is None
        assert batch.clients_applied_at is not None
        assert parts[0].confirmed_at is not None and parts[1].confirmed_at is None
        assert parts[1].retry_count == 1

        replay = delivery(batch)
        await consumer.process_delivery(replay, sessions, settings, clients, work)
        replay.ack.assert_awaited_once()

    assert [item["delivery"]["part_number"] for item in calls] == [1, 2, 2]
    assert calls[1] == calls[2]
    assert batch.status == Status.COMPLETED and batch.completed_at is not None
    assert parts[1].confirmed_at is not None
    assert not list(settings.upload_dir.iterdir())


async def test_snapshot_persists_a_terminal_rejection_until_reconciliation(
    batch: ImportBatch,
    db: AsyncMock,
    sessions: MagicMock,
) -> None:
    part = OperatorDelivery(
        batch_id=batch.id,
        part_number=1,
        payload={"batch_id": str(batch.id), "clients": []},
    )
    db.scalar.return_value = part
    calls = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(409)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), base_url="http://work"
    ) as work:
        clients = AsyncMock(spec=httpx.AsyncClient)
        assert not await consumer.complete_snapshot(sessions, batch.id, clients, work)
        assert not await consumer.complete_snapshot(sessions, batch.id, clients, work)
        clients.get.assert_not_awaited()

    assert len(calls) == 1
    assert part.rejected_status == 409 and part.confirmed_at is None
    assert batch.status == Status.PROCESSING and batch.completed_at is None


async def test_snapshot_without_remaining_clients_finishes_without_a_delivery(
    batch: ImportBatch,
    db: AsyncMock,
    sessions: MagicMock,
) -> None:
    clients = AsyncMock(spec=httpx.AsyncClient)
    clients.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "http://clients"),
        json={"batch_id": str(batch.id), "limit": 1000, "offset": 0, "total": 0, "items": []},
    )
    assert await consumer.complete_snapshot(sessions, batch.id, clients, None)
    assert batch.status == Status.COMPLETED and batch.completed_at is not None
    db.add_all.assert_not_called()


@pytest.mark.parametrize(("header", "delay"), [("90", 90), ("invalid", 0), ("", 0)])
def test_retry_after_handles_limits_and_invalid_values(header: str, delay: int) -> None:
    request = httpx.Request("PUT", "http://work")
    response = httpx.Response(429, headers={"Retry-After": header}, request=request)
    with pytest.raises(httpx.HTTPStatusError) as failure:
        response.raise_for_status()
    assert consumer.retry_after(failure.value) == delay
    assert consumer.retry_after(httpx.ReadTimeout("timed out")) == 0
