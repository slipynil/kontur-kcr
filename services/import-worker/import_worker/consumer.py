import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import UUID

import aio_pika
import httpx
from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from import_worker.broker import QUEUE_ARGUMENTS, QUEUE_NAME, RETRY_DELAY, Job
from import_worker.clients import ApplyRejected, apply_import, fingerprint
from import_worker.config import Settings
from import_worker.database import (
    ImportBatch,
    ImportObservation,
    OperatorDelivery,
    active_rollback,
    add_errors,
    applied_fingerprints,
    lock_imports,
)
from import_worker.models import ImportErrorRow, ImportParameters, ImportType, ParsedImport, Status
from import_worker.operator_work import prepare_deliveries, send_delivery
from import_worker.parser import InvalidWorkbook, parse_workbook
from import_worker.storage import upload_path

logger = logging.getLogger(__name__)
CLIENT_ERRORS = {
    "UNKNOWN_KCR_CODE": "Код КЦР отсутствует в реестре",
    "MISSING_REQUIRED_VALUE": "Нет обязательного значения в файле и в предыдущих данных",
    "DUPLICATE_ROW": "Дубликат строки в пакете",
}


class SnapshotRetry(Exception):
    def __init__(self, delay: float) -> None:
        self.delay = delay


def retry_after(error: httpx.HTTPError) -> float:
    if not isinstance(error, httpx.HTTPStatusError):
        return 0
    value = error.response.headers.get("Retry-After", "")
    if value.isascii() and value.isdecimal() and len(value) <= 9:
        return int(value)
    try:
        return (parsedate_to_datetime(value).astimezone(UTC) - datetime.now(UTC)).total_seconds()
    except TypeError, ValueError, OverflowError:
        return 0


def finish(batch: ImportBatch, status: Status) -> None:
    batch.status = status
    batch.completed_at = func.clock_timestamp()
    batch.apply_payload = None
    batch.apply_started_at = None


def file_error(reason: str) -> ImportErrorRow:
    return ImportErrorRow(sheet="", row_number=0, reason=reason)


async def prepare(sessions: async_sessionmaker[AsyncSession], batch_id: UUID, path: Path) -> bool:
    async with sessions.begin() as db:
        batch = await db.get(ImportBatch, batch_id, with_for_update=True)
        if batch is None or batch.status != Status.PROCESSING:
            return False
        if batch.clients_applied_at is not None or batch.apply_payload is not None:
            return True

        params = ImportParameters.model_validate(batch, from_attributes=True)
        try:
            parsed = await asyncio.to_thread(parse_workbook, path, params, batch.file_sha256)
        except InvalidWorkbook as exc:
            add_errors(db, batch, [file_error(str(exc))])
            finish(batch, Status.FAILED)
            return False

        batch.total_rows = parsed.total_rows
        add_errors(db, batch, parsed.errors)
        if not parsed.rows:
            if not parsed.errors:
                add_errors(db, batch, [file_error("Книга не содержит строк для импорта")])
            finish(batch, Status.FAILED)
            return False
        batch.apply_payload = ParsedImport(rows=parsed.rows).model_dump(
            mode="json", exclude_unset=True
        )
        return True


async def claim_apply(sessions: async_sessionmaker[AsyncSession], batch_id: UUID) -> UUID | None:
    async with sessions.begin() as db:
        await lock_imports(db)
        if await active_rollback(db) is not None:
            raise RuntimeError("Ожидается завершение отката пакета")
        current = await db.scalar(
            select(ImportBatch.id)
            .where(
                ImportBatch.status == Status.PROCESSING,
                or_(
                    ImportBatch.apply_started_at.is_not(None),
                    ImportBatch.clients_applied_at.is_not(None),
                ),
            )
            .order_by(ImportBatch.apply_started_at.is_(None), ImportBatch.clients_applied_at)
            .limit(1)
        )
        if current is not None:
            return current

        batch = await db.get(ImportBatch, batch_id, with_for_update=True)
        if batch is None or batch.apply_payload is None:
            return None
        params = ImportParameters.model_validate(batch, from_attributes=True)
        parsed = ParsedImport.model_validate(batch.apply_payload)
        keys = [fingerprint(params, row) for row in parsed.rows]
        already_applied = await applied_fingerprints(db, keys)
        remaining = [
            row for row, key in zip(parsed.rows, keys, strict=True) if key not in already_applied
        ]
        batch.success_count = len(parsed.rows) - len(remaining)
        if not remaining:
            finish(batch, Status.COMPLETED)
            return None
        batch.apply_payload = ParsedImport(rows=remaining).model_dump(
            mode="json", exclude_unset=True
        )
        batch.apply_started_at = func.clock_timestamp()
        return batch_id


async def apply_claimed(
    sessions: async_sessionmaker[AsyncSession],
    batch_id: UUID,
    clients: httpx.AsyncClient,
) -> bool:
    async with sessions.begin() as db:
        await lock_imports(db)
        batch = await db.get(ImportBatch, batch_id, with_for_update=True)
        if batch is None or batch.status != Status.PROCESSING:
            return False
        if batch.clients_applied_at is not None:
            if batch.import_type == ImportType.REGISTRY:
                finish(batch, Status.COMPLETED)
                return False
            if batch.apply_started_at is None:
                batch.apply_started_at = func.clock_timestamp()
            return True
        if batch.apply_payload is None:
            return False

        parsed = ParsedImport.model_validate(batch.apply_payload)
        params = ImportParameters.model_validate(batch, from_attributes=True)
        try:
            result = await apply_import(clients, batch.id, batch.uploaded_by, params, parsed)
        except ApplyRejected:
            add_errors(db, batch, [file_error("Clients Service отклонил параметры пакета")])
            finish(batch, Status.FAILED)
            return False
        errors = [
            parsed.rows[error.row_number - 1].error(
                CLIENT_ERRORS.get(error.code, "Clients Service отклонил строку")
                + (f" ({error.field})" if error.field else "")
            )
            for error in result.errors
        ]
        add_errors(db, batch, errors)
        batch.success_count += result.accepted_rows
        if result.accepted_rows:
            rejected = {error.row_number for error in result.errors}
            accepted = {
                fingerprint(params, row)
                for index, row in enumerate(parsed.rows, start=1)
                if index not in rejected
            }
            db.add_all([ImportObservation(batch_id=batch.id, fingerprint=key) for key in accepted])
            batch.clients_applied_at = func.clock_timestamp()
            batch.apply_payload = None
            if batch.import_type == ImportType.SNAPSHOT:
                return True
        finish(batch, Status.COMPLETED if batch.success_count else Status.FAILED)
        return False


async def complete_snapshot(
    sessions: async_sessionmaker[AsyncSession],
    batch_id: UUID,
    clients: httpx.AsyncClient,
    operator_work: httpx.AsyncClient | None,
) -> bool:
    while True:
        async with sessions.begin() as db:
            await lock_imports(db)
            batch = await db.get(ImportBatch, batch_id, with_for_update=True)
            if batch is None or batch.status != Status.PROCESSING:
                return True
            delivery = await db.scalar(
                select(OperatorDelivery)
                .where(
                    OperatorDelivery.batch_id == batch_id, OperatorDelivery.confirmed_at.is_(None)
                )
                .order_by(OperatorDelivery.part_number)
                .limit(1)
            )
            if delivery is None:
                deliveries = await prepare_deliveries(clients, batch_id)
                if not deliveries:
                    finish(batch, Status.COMPLETED)
                    return True
                db.add_all(deliveries)
                continue
            if delivery.rejected_status is not None:
                return False
            if operator_work is None:
                raise RuntimeError("Не настроен адрес Operator Work")
            try:
                rejected_status = await send_delivery(
                    operator_work, batch.selected_quarter, batch.uploaded_by, delivery.payload
                )
            except httpx.HTTPError as exc:
                delay = min(60, RETRY_DELAY * 2 ** min(delivery.retry_count, 5))
                delivery.retry_count += 1
                retry = SnapshotRetry(max(delay, retry_after(exc)))
            else:
                if rejected_status is not None:
                    delivery.rejected_status = rejected_status
                    logger.error(
                        "Operator Work отклонил часть %s пакета %s (HTTP %s)",
                        delivery.part_number,
                        batch_id,
                        rejected_status,
                    )
                    return False
                delivery.confirmed_at = func.clock_timestamp()
                if delivery.part_number == delivery.payload.get("delivery", {}).get(
                    "part_count", 1
                ):
                    finish(batch, Status.COMPLETED)
                    return True
                continue
        raise retry


async def apply_prepared(
    sessions: async_sessionmaker[AsyncSession],
    batch_id: UUID,
    clients: httpx.AsyncClient | None,
    operator_work: httpx.AsyncClient | None,
) -> None:
    if clients is None:
        raise RuntimeError("Локальное применение Clients Service выключено")
    # A lost HTTP response must be reconciled before a different batch can apply.
    while (claimed := await claim_apply(sessions, batch_id)) is not None:
        pending = await apply_claimed(sessions, claimed, clients)
        completed = not pending or await complete_snapshot(
            sessions, claimed, clients, operator_work
        )
        if claimed == batch_id:
            return
        if not completed:
            raise RuntimeError(f"Ожидается сверка пакета {claimed}")


async def process_delivery(
    message: AbstractIncomingMessage,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    clients: httpx.AsyncClient | None,
    operator_work: httpx.AsyncClient | None = None,
) -> None:
    try:
        job = Job.model_validate_json(message.body)
    except ValidationError:
        logger.warning("Некорректное сообщение импорта")
        await message.reject(requeue=False)
        return

    try:
        path = upload_path(settings.upload_dir, job.batch_id)
        if await prepare(sessions, job.batch_id, path):
            await apply_prepared(sessions, job.batch_id, clients, operator_work)
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception as exc:
        logger.warning("Пакет %s будет обработан повторно (%s)", job.batch_id, type(exc).__name__)
        await asyncio.sleep(exc.delay if isinstance(exc, SnapshotRetry) else RETRY_DELAY)
        await message.reject(requeue=True)
        return
    await message.ack()


async def consume(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    clients: httpx.AsyncClient | None,
    operator_work: httpx.AsyncClient | None = None,
) -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.amqp_url, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)
                queue = await channel.declare_queue(
                    QUEUE_NAME, durable=True, arguments=QUEUE_ARGUMENTS
                )
                async with queue.iterator() as deliveries:
                    async for message in deliveries:
                        await process_delivery(message, sessions, settings, clients, operator_work)
        except Exception as exc:
            logger.warning("Подключение Worker будет повторено (%s)", type(exc).__name__)
            await asyncio.sleep(RETRY_DELAY)


async def run(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine)
        async with AsyncExitStack() as stack:
            clients = operator_work = None
            if settings.clients_url:
                clients = await stack.enter_async_context(
                    httpx.AsyncClient(base_url=settings.clients_url, timeout=30)
                )
            if settings.operator_work_url:
                operator_work = await stack.enter_async_context(
                    httpx.AsyncClient(base_url=settings.operator_work_url, timeout=30)
                )
            await consume(settings, sessions, clients, operator_work)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(Settings.from_env()))
