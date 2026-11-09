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
from conftest import WorkbookFactory
from pamqp.commands import Basic

from import_worker import broker, consumer
from import_worker.broker import Job
from import_worker.clients import fingerprint
from import_worker.config import Settings
from import_worker.database import ImportBatch
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
) -> None:
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    db.scalar.side_effect = [None, batch.id]
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
    assert [item["client"]["kcr_code"] for item in requests[0]["items"]] == ["001", "002"]
    assert (batch.total_rows, batch.success_count, batch.error_count) == (2, 1, 1)
    error = db.add_all.call_args_list[-2].args[0][0]
    assert (error.sheet, error.row_number, error.kcr_code) == ("Срез", 2, "002")
    observation = db.add_all.call_args.args[0][0]
    assert observation.batch_id == batch.id
    assert batch.apply_payload is None and batch.clients_applied_at is not None
    assert not list(settings.upload_dir.iterdir())


async def test_duplicate_observations_complete_without_http(
    settings: Settings, batch: ImportBatch, db: AsyncMock, sessions: MagicMock
) -> None:
    path = settings.upload_dir / f"{batch.id}.xlsx"
    assert await consumer.prepare(sessions, batch.id, path)
    params = ImportParameters.model_validate(batch, from_attributes=True)
    parsed = ParsedImport.model_validate(batch.apply_payload)
    db.scalars.return_value = [fingerprint(params, row) for row in parsed.rows]
    clients = AsyncMock(spec=httpx.AsyncClient)
    message = delivery(batch)
    await consumer.process_delivery(message, sessions, settings, clients)
    message.ack.assert_awaited_once()
    clients.post.assert_not_awaited()
    assert batch.status == Status.COMPLETED and batch.success_count == 2
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
