import asyncio
import logging
from pathlib import Path
from uuid import UUID

import aio_pika
import httpx
from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from import_worker.broker import QUEUE_ARGUMENTS, QUEUE_NAME, RETRY_DELAY, Job
from import_worker.clients import ApplyRejected, apply_import, fingerprint
from import_worker.config import Settings
from import_worker.database import ImportBatch, ImportObservation, add_errors, applied_fingerprints
from import_worker.models import ImportErrorRow, ImportParameters, ParsedImport, Status
from import_worker.parser import InvalidWorkbook, parse_workbook
from import_worker.storage import upload_path

logger = logging.getLogger(__name__)
APPLY_LOCK = 0x494D504F5254
CLIENT_ERRORS = {
    "UNKNOWN_KCR_CODE": "Код КЦР отсутствует в реестре",
    "MISSING_REQUIRED_VALUE": "Нет обязательного значения в файле и в предыдущих данных",
    "DUPLICATE_ROW": "Дубликат строки в пакете",
}


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
        if batch is None or batch.status != Status.PROCESSING or batch.clients_applied_at:
            return False
        if batch.apply_payload is not None:
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
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPLY_LOCK})
        current = await db.scalar(
            select(ImportBatch.id).where(
                ImportBatch.apply_started_at.is_not(None), ImportBatch.apply_payload.is_not(None)
            )
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
) -> None:
    async with sessions.begin() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPLY_LOCK})
        batch = await db.get(ImportBatch, batch_id, with_for_update=True)
        if batch is None or batch.apply_payload is None:
            return

        parsed = ParsedImport.model_validate(batch.apply_payload)
        params = ImportParameters.model_validate(batch, from_attributes=True)
        try:
            result = await apply_import(clients, batch.id, batch.uploaded_by, params, parsed)
        except ApplyRejected:
            add_errors(db, batch, [file_error("Clients Service отклонил параметры пакета")])
            finish(batch, Status.FAILED)
            return
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
        else:
            finish(batch, Status.COMPLETED if batch.success_count else Status.FAILED)


async def apply_prepared(
    sessions: async_sessionmaker[AsyncSession],
    batch_id: UUID,
    clients: httpx.AsyncClient | None,
) -> None:
    if clients is None:
        raise RuntimeError("Локальное применение Clients Service выключено")
    # A lost HTTP response must be reconciled before a different batch can apply.
    while (claimed := await claim_apply(sessions, batch_id)) is not None:
        await apply_claimed(sessions, claimed, clients)
        if claimed == batch_id:
            return


async def process_delivery(
    message: AbstractIncomingMessage,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    clients: httpx.AsyncClient | None,
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
            await apply_prepared(sessions, job.batch_id, clients)
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception as exc:
        logger.warning("Пакет %s будет обработан повторно (%s)", job.batch_id, type(exc).__name__)
        await asyncio.sleep(RETRY_DELAY)
        await message.reject(requeue=True)
        return
    await message.ack()


async def consume(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    clients: httpx.AsyncClient | None,
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
                        await process_delivery(message, sessions, settings, clients)
        except Exception as exc:
            logger.warning("Подключение Worker будет повторено (%s)", type(exc).__name__)
            await asyncio.sleep(RETRY_DELAY)


async def run(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine)
        if settings.clients_url:
            async with httpx.AsyncClient(base_url=settings.clients_url, timeout=30) as clients:
                await consume(settings, sessions, clients)
        else:
            await consume(settings, sessions, None)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(Settings.from_env()))
