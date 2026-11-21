import json
import os
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import aio_pika
import httpx
import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel
from conftest import WorkbookFactory
from openpyxl import load_workbook
from pamqp.commands import Basic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from import_worker import consumer
from import_worker.api import create_app
from import_worker.broker import QUEUE_ARGUMENTS, QUEUE_NAME, Job, publish_one
from import_worker.config import Settings
from import_worker.database import ImportBatch, OperatorDelivery
from import_worker.error_report import XLSX_MEDIA_TYPE


async def upload(
    client: httpx.AsyncClient, content: bytes, kind: str, date: str = "", quarter: str = "2026_Q2"
) -> UUID:
    data = {"import_type": kind}
    if kind == "SNAPSHOT":
        data.update(selected_quarter=quarter, snapshot_date=date)
    response = await client.post(
        "/internal/v1/imports",
        headers={"X-User-Id": str(uuid4())},
        files={"file": ("test.xlsx", content)},
        data=data,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PROCESSING"
    return UUID(response.json()["id"])


def delivery(body: bytes) -> AsyncMock:
    return AsyncMock(spec=AbstractIncomingMessage, body=body)


async def test_invalid_delivery(tmp_path: Path) -> None:
    message = delivery(b'{"batch_id": "invalid", "path": "/file.xlsx"}')
    settings = Settings("postgresql+asyncpg://unused", "amqp://unused", tmp_path)
    await consumer.process_delivery(message, AsyncMock(), settings, None)
    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()


async def test_worker_records_excel_errors_without_clients(
    tmp_path: Path, xlsx: WorkbookFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = os.getenv("IMPORT_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("Укажите Import DB с применёнными миграциями")
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    settings = Settings(db_url, "amqp://unused", tmp_path)
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine)
    exchange = AsyncMock()
    exchange.publish.return_value = Basic.Ack(delivery_tag=1)
    channel = cast(AbstractRobustChannel, AsyncMock(default_exchange=exchange))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://import"
        ) as api:
            invalid_only = xlsx({"Срез": [["bad", None, None, 9, -1]]})
            batch_id = await upload(api, invalid_only, "SNAPSHOT", "2026-04-10")
            assert await publish_one(sessions, channel)
            message = delivery(exchange.publish.await_args.args[0].body)
            assert Job.model_validate_json(message.body).batch_id == batch_id
            await consumer.process_delivery(message, sessions, settings, None)
            message.ack.assert_awaited_once()
            report = (await api.get(f"/internal/v1/imports/{batch_id}")).json()
            assert report["status"] == "FAILED"
            assert report["error_log"][0]["row_number"] == 1
            assert not list(tmp_path.iterdir())

            mixed = xlsx({"Срез": [["bad", None, None, 9, -1], ["001", None, None, 9, 1]]})
            batch_id = await upload(api, mixed, "SNAPSHOT", "2026-04-10")
            assert await publish_one(sessions, channel)
            message = delivery(exchange.publish.await_args.args[0].body)
            assert Job.model_validate_json(message.body).batch_id == batch_id
            await consumer.process_delivery(message, sessions, settings, None)
            message.reject.assert_awaited_once_with(requeue=True)
            message.ack.assert_not_awaited()
            report = (await api.get(f"/internal/v1/imports/{batch_id}")).json()
            assert report["status"] == "PROCESSING"
            assert report["clients_applied_at"] is None and report["error_count"] == 1
            async with sessions() as db:
                batch = await db.get(ImportBatch, batch_id)
                assert batch.apply_payload["rows"][0]["row_number"] == 2
    finally:
        await engine.dispose()


async def test_postgres_upload_retry_and_report(
    tmp_path: Path, xlsx: WorkbookFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = os.getenv("IMPORT_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("Укажите Import DB с применёнными миграциями")
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    settings = Settings(db_url, "amqp://unused", tmp_path)
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine)
    app = create_app(settings)
    calls: list[dict] = []
    work_calls: list[dict] = []
    kcr = f"test-{uuid4()}"
    in_registry = True

    def clients_reply(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "batch_id": request.url.path.split("/")[-2],
                    "items": [{"kcr_code": kcr, "outstanding_documents": 0}],
                    "total": 1,
                    "limit": 1000,
                    "offset": 0,
                },
            )
        assert request.url.path == "/internal/v1/import/apply"
        envelope = json.loads(request.content)
        calls.append(envelope)
        verified = 0
        if envelope["import_type"] == "REGISTRY":
            assert {"selected_quarter", "snapshot_date"}.isdisjoint(envelope)
            assert envelope["items"][0]["client"]["is_blocked"] is False
            assert envelope["items"][0]["client"]["manager_name"] is None
            assert "snapshot" not in envelope["items"][0]
            errors = []
        else:
            if len(calls) == 2:
                raise httpx.ReadTimeout("Ответ после применения потерян")
            assert envelope["items"][0]["client"] == {"kcr_code": kcr}
            assert envelope["items"][0]["snapshot"] == {
                "quarter": "2026_Q1",
                "snapshot_date": "2026-04-10",
                "total_documents": None,
                "unsubmitted_documents": 0,
            }
            if envelope["items"][0].get("verify_only"):
                assert len(envelope["items"]) == 1
                verified = int(in_registry)
                unknown_indices = [] if in_registry else [1]
            else:
                assert len(envelope["items"]) == 2
                unknown_indices = [2]
            errors = [
                {"row_number": index, "code": "UNKNOWN_KCR_CODE", "field": "client.kcr_code"}
                for index in unknown_indices
            ]
        return httpx.Response(
            200,
            json={
                "batch_id": envelope["batch_id"],
                "accepted_rows": len(envelope["items"]) - len(errors) - verified,
                "verified_rows": verified,
                "rejected_rows": len(errors),
                "errors": errors,
            },
        )

    def work_reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        work_calls.append(body)
        assert request.url.path == "/internal/v1/cycles/apply-snapshot"
        assert request.url.params["work_quarter"] == "2026_Q2"
        assert request.headers["x-user-role"] == "ADMIN"
        assert body["clients"] == [{"kcr_code": kcr, "outstanding_documents": 0}]
        if len(work_calls) == 1:
            raise httpx.ReadTimeout("Подтверждение Operator Work потеряно")
        return httpx.Response(204)

    exchange = AsyncMock()
    exchange.publish.return_value = Basic.Ack(delivery_tag=1)
    channel = cast(AbstractRobustChannel, AsyncMock(default_exchange=exchange))
    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://import"
            ) as api,
            httpx.AsyncClient(
                transport=httpx.MockTransport(clients_reply), base_url="http://clients"
            ) as clients,
            httpx.AsyncClient(
                transport=httpx.MockTransport(work_reply), base_url="http://operator-work"
            ) as operator_work,
        ):
            registry = xlsx(
                {
                    "Реестр": [
                        [kcr, None, "Клиент", None, None, None, None, "001234", None, None, False]
                    ]
                }
            )
            registry_id = await upload(api, registry, "REGISTRY")
            assert await publish_one(sessions, channel)
            published = exchange.publish.await_args.args[0]
            assert Job.model_validate_json(published.body).batch_id == registry_id
            assert published.delivery_mode is aio_pika.DeliveryMode.PERSISTENT
            first = delivery(published.body)
            await consumer.process_delivery(first, sessions, settings, clients)
            first.ack.assert_awaited_once()
            report = (await api.get(f"/internal/v1/imports/{registry_id}")).json()
            assert report["status"] == "COMPLETED" and report["completed_at"]
            assert {"selected_quarter", "snapshot_date"}.isdisjoint(report)
            async with sessions() as db:
                batch = await db.get(ImportBatch, registry_id)
                assert batch.selected_quarter is None and batch.snapshot_date is None
            assert not work_calls

            header = ["Код КЦР", None, None, None, None, "Исходный квартал"]
            valid = [kcr, None, None, None, 0, "2026_Q1"]
            snapshot = xlsx(
                {
                    "2 кв 2026": [
                        header,
                        ["bad", None, None, -1, 2, "2026_Q2"],
                        valid,
                        ["404", None, None, 9, 2, "2026_Q2"],
                    ]
                }
            )
            snapshot_id = await upload(api, snapshot, "SNAPSHOT", "2026-04-10")
            exchange.publish.side_effect = ConnectionError
            with pytest.raises(ConnectionError):
                await publish_one(sessions, channel)
            async with sessions() as db:
                assert (await db.get(ImportBatch, snapshot_id)).published_at is None
            exchange.publish.side_effect = None
            assert await publish_one(sessions, channel)
            assert not await publish_one(sessions, channel)

            body = exchange.publish.await_args.args[0].body
            first = delivery(body)
            await consumer.process_delivery(first, sessions, settings, clients, operator_work)
            first.reject.assert_awaited_once_with(requeue=True)
            first.ack.assert_not_awaited()

            second = delivery(body)
            await consumer.process_delivery(second, sessions, settings, clients, operator_work)
            second.reject.assert_awaited_once_with(requeue=True)
            assert calls[1] == calls[2]

            report = (await api.get(f"/internal/v1/imports/{snapshot_id}")).json()
            assert report["status"] == "PROCESSING"
            assert report["clients_applied_at"] and report["completed_at"] is None
            third = delivery(body)
            await consumer.process_delivery(third, sessions, settings, clients, operator_work)
            third.ack.assert_awaited_once()
            assert len(work_calls) == 2 and work_calls[0] == work_calls[1]
            assert not list(tmp_path.iterdir())

            report = (await api.get(f"/internal/v1/imports/{snapshot_id}")).json()
            assert report["status"] == "COMPLETED" and report["completed_at"]
            assert (report["total_rows"], report["success_count"], report["error_count"]) == (
                3,
                1,
                2,
            )
            assert [(row["row_number"], row["kcr_code"]) for row in report["error_log"]] == [
                (2, "bad"),
                (4, "404"),
            ]
            assert "отсутствует в реестре" in report["error_log"][1]["reason"]
            download = await api.get(f"/internal/v1/imports/{snapshot_id}/errors.xlsx")
            assert (
                download.status_code == 200 and download.headers["content-type"] == XLSX_MEDIA_TYPE
            )
            assert download.headers["cache-control"] == "no-store"
            assert download.headers["content-disposition"].startswith("attachment;")
            workbook = load_workbook(BytesIO(download.content), read_only=True)
            try:
                rows = list(workbook.active.values)
                assert rows[1:] == [
                    (row["sheet"], row["row_number"], row["kcr_code"], row["reason"])
                    for row in report["error_log"]
                ]
            finally:
                workbook.close()
            assert (await api.get(f"/internal/v1/imports/{uuid4()}/errors.xlsx")).status_code == 404
            assert (
                await api.post(f"/internal/v1/imports/{registry_id}/rollback")
            ).status_code == 409
            assert (
                await api.post(f"/internal/v1/imports/{snapshot_id}/rollback")
            ).status_code == 503
            assert (await api.post(f"/internal/v1/imports/{uuid4()}/rollback")).status_code == 404

            # Recheck the code for an identical observation without writing a new one.
            copy = xlsx({"Другой лист": [header, [], valid]})
            for in_registry in (True, False):
                duplicate = await upload(api, copy, "SNAPSHOT", "2026-04-10")
                assert await publish_one(sessions, channel)
                replay = delivery(exchange.publish.await_args.args[0].body)
                await consumer.process_delivery(replay, sessions, settings, clients)
                replay.ack.assert_awaited_once()
                result = (await api.get(f"/internal/v1/imports/{duplicate}")).json()
                assert result["clients_applied_at"] is None
                if in_registry:
                    assert result["status"] == "COMPLETED" and result["success_count"] == 1
                    assert result["error_log"] == []
                else:
                    assert result["status"] == "FAILED" and result["success_count"] == 0
                    assert result["error_log"] == [
                        {
                            "sheet": "Другой лист",
                            "row_number": 3,
                            "kcr_code": kcr,
                            "reason": "Код КЦР отсутствует в реестре (client.kcr_code)",
                        }
                    ]
            assert len(calls) == 5
    finally:
        await engine.dispose()


async def test_resumes_legacy_snapshot_and_only_resends_unconfirmed_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_url = os.getenv("IMPORT_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("Укажите Import DB с применёнными миграциями")
    monkeypatch.setattr(consumer, "RETRY_DELAY", 0)
    settings = Settings(db_url, "amqp://unused", tmp_path)
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine)
    batch_id, admin_id = uuid4(), uuid4()
    offsets = []
    calls = []

    def clients_reply(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "batch_id": str(batch_id),
                "limit": 1000,
                "offset": offset,
                "total": 1002,
                "items": [
                    {"kcr_code": f"C{index:04}", "outstanding_documents": None if index == 0 else 7}
                    for index in range(offset, min(offset + 1000, 1002))
                ],
            },
        )

    def work_reply(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-user-id"] == str(admin_id)
        assert request.url.params["work_quarter"] == "2026_Q2"
        calls.append(json.loads(request.content))
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(204)

    try:
        async with sessions.begin() as db:
            db.add(
                ImportBatch(
                    id=batch_id,
                    uploaded_by=admin_id,
                    filename="missing.xlsx",
                    selected_quarter="2026_Q2",
                    import_type="SNAPSHOT",
                    snapshot_date=date(2026, 4, 10),
                    clients_applied_at=datetime.now(UTC),
                )
            )
        exchange = AsyncMock()
        exchange.publish.return_value = Basic.Ack(delivery_tag=1)
        channel = cast(AbstractRobustChannel, AsyncMock(default_exchange=exchange))
        assert await publish_one(sessions, channel)
        body = exchange.publish.await_args.args[0].body
        assert Job.model_validate_json(body).batch_id == batch_id
        for attempt in range(2):
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(clients_reply), base_url="http://clients"
                ) as clients,
                httpx.AsyncClient(
                    transport=httpx.MockTransport(work_reply), base_url="http://operator-work"
                ) as operator_work,
            ):
                message = delivery(body)
                await consumer.process_delivery(message, sessions, settings, clients, operator_work)
            async with sessions() as db:
                batch = await db.get(ImportBatch, batch_id)
                parts = list(
                    await db.scalars(
                        select(OperatorDelivery)
                        .where(OperatorDelivery.batch_id == batch_id)
                        .order_by(OperatorDelivery.part_number)
                    )
                )
                assert parts[0].confirmed_at is not None
                assert parts[1].retry_count == 1
                if attempt == 0:
                    message.reject.assert_awaited_once_with(requeue=True)
                    assert batch.status == "PROCESSING" and batch.completed_at is None
                    assert parts[1].confirmed_at is None
                else:
                    message.ack.assert_awaited_once()
                    assert batch.status == "COMPLETED" and batch.completed_at is not None
                    assert parts[1].confirmed_at is not None
        assert offsets == [0, 1000]
        assert len(calls) == 3 and calls[1] == calls[2]
        assert [len(call["clients"]) for call in calls] == [1000, 1, 1]
        assert calls[0]["clients"][0]["kcr_code"] == "C0001"
        assert calls[0]["delivery"]["part_count"] == 2
        assert calls[0]["delivery"]["delivery_id"] != calls[1]["delivery"]["delivery_id"]
    finally:
        await engine.dispose()


async def test_rabbitmq_redelivery() -> None:
    url = os.getenv("IMPORT_TEST_AMQP_URL")
    if not url:
        pytest.skip("Укажите отдельный экземпляр RabbitMQ для интеграционного теста")
    connection = await aio_pika.connect_robust(url, timeout=5)
    async with connection:
        channel = await connection.channel(on_return_raises=True)
        queue = await channel.declare_queue(QUEUE_NAME, durable=True, arguments=QUEUE_ARGUMENTS)
        job = Job(batch_id=uuid4())
        confirmation = await channel.default_exchange.publish(
            aio_pika.Message(
                job.model_dump_json().encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=queue.name,
            mandatory=True,
        )
        assert isinstance(confirmation, Basic.Ack)
        first = await queue.get(timeout=5)
        assert Job.model_validate_json(first.body) == job
        await channel.close()
        restarted = await connection.channel()
        queue = await restarted.declare_queue(QUEUE_NAME, durable=True, arguments=QUEUE_ARGUMENTS)
        second = await queue.get(timeout=5)
        assert second.redelivered
        assert Job.model_validate_json(second.body) == job
        await second.ack()


async def test_live_clients_quarter_history(tmp_path: Path, xlsx: WorkbookFactory) -> None:
    db_url = os.getenv("IMPORT_TEST_DATABASE_URL")
    clients_url = os.getenv("IMPORT_TEST_CLIENTS_URL")
    work_url = os.getenv("IMPORT_TEST_OPERATOR_WORK_URL")
    if not db_url or not clients_url or not work_url:
        pytest.skip("Укажите изолированные Import DB, Clients Service и Operator Work")
    settings = Settings(db_url, "amqp://unused", tmp_path)
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine)
    app = create_app(settings)
    kcr = f"test-{uuid4()}"
    exchange = AsyncMock()
    exchange.publish.return_value = Basic.Ack(delivery_tag=1)
    channel = cast(AbstractRobustChannel, AsyncMock(default_exchange=exchange))
    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://import"
            ) as api,
            httpx.AsyncClient(base_url=clients_url) as clients,
            httpx.AsyncClient(base_url=work_url) as operator_work,
        ):

            async def submit(content: bytes, kind: str, date: str, quarter: str) -> dict:
                batch_id = await upload(api, content, kind, date, quarter)
                assert await publish_one(sessions, channel)
                body = exchange.publish.await_args.args[0].body
                assert Job.model_validate_json(body).batch_id == batch_id
                message = delivery(body)
                await consumer.process_delivery(message, sessions, settings, clients, operator_work)
                message.ack.assert_awaited_once()
                response = await api.get(f"/internal/v1/imports/{batch_id}")
                response.raise_for_status()
                return response.json()

            registry = xlsx(
                {"Реестр": [[kcr, None, "Клиент", None, None, None, None, "0000000001"]]}
            )
            result = await submit(registry, "REGISTRY", "2026-03-30", "2026_Q1")
            assert result["clients_applied_at"]
            work_path = f"/internal/v1/clients/{kcr}"
            admin = {"X-User-Id": str(uuid4()), "X-User-Role": "ADMIN"}
            operator = {"X-User-Id": str(uuid4()), "X-User-Role": "OPERATOR"}
            first_quarter = {"work_quarter": "2026_Q1"}
            wait_until = "2036-11-21"
            header = ["Код КЦР", None, None, None, None, "Исходный квартал"]
            cases = [
                ("2026_Q1", "2026-03-30", 111, 10),
                ("2026_Q2", "2026-04-05", None, 6),
                ("2026_Q2", "2026-04-10", None, None),
                ("2026_Q2", "2026-04-12", None, 6),
                ("2026_Q1", "2026-03-15", 999, 12),
                ("2026_Q2", "2026-04-12", None, 6),
            ]
            for quarter, date, total, remainder in cases:
                content = xlsx({"Срез": [header, [kcr, None, None, total, remainder, "2026_Q1"]]})
                result = await submit(content, "SNAPSHOT", date, quarter)
                assert result["status"] == "COMPLETED" and result["completed_at"]
                assert result["success_count"] == 1 and not result["error_log"]
                if date == "2026-03-30":
                    published = await operator_work.put(
                        f"{work_path}/publication", params=first_quarter, headers=admin
                    )
                    published.raise_for_status()
                    assigned = await operator_work.post(
                        f"{work_path}/take",
                        params=first_quarter,
                        headers=operator,
                        json={"request_id": str(uuid4()), "expected_assignment_id": None},
                    )
                    assigned.raise_for_status()
                    event = await operator_work.post(
                        f"{work_path}/events",
                        headers=operator,
                        json={
                            **first_quarter,
                            "event_type": "CALL_RESULT",
                            "request_id": str(uuid4()),
                            "expected_assignment_id": assigned.json()["assignment_id"],
                            "call_result": "AGREEMENT",
                            "promised_date": wait_until,
                            "wait_until": wait_until,
                        },
                    )
                    event.raise_for_status()
            assert result["status"] == "COMPLETED" and result["clients_applied_at"] is None

            response = await operator_work.get(f"{work_path}/cycles", headers=admin)
            response.raise_for_status()
            cycles = {item["work_quarter"]: item for item in response.json()["items"]}
            assert set(cycles) == {"2026_Q1", "2026_Q2"}
            current = cycles["2026_Q2"]
            assert current["is_current"] and current["published_at"] is None
            assert current["stage"] == "WAIT_DEADLINE" and current["wait_until"] == wait_until
            assert current["assigned_operator_id"] == operator["X-User-Id"]

            card = await clients.get(f"/internal/v1/clients/{kcr}", params={"quarter": "2026_Q2"})
            card.raise_for_status()
            assert len(card.json()["snapshots"]) == 1
            snapshot = card.json()["snapshots"][0]
            assert snapshot["quarter"] == "2026_Q1"
            assert snapshot["last_observation_quarter"] == "2026_Q2"
            assert snapshot["total_documents"] == 111
            assert snapshot["unsubmitted_documents"] == card.json()["outstanding_documents"] == 6

            history = await clients.get(f"/internal/v1/clients/{kcr}/snapshots/history")
            history.raise_for_status()
            records = {row["snapshot_date"]: row for row in history.json()["items"]}
            assert history.json()["total"] == len(records) == 5
            assert records["2026-03-15"]["unsubmitted_documents"] == 12
            assert records["2026-04-10"]["unsubmitted_documents"] == 6
            assert records["2026-04-10"]["inherited_fields"]["unsubmitted_documents"][
                "snapshot_date"
            ] == ("2026-04-05")
    finally:
        await engine.dispose()
