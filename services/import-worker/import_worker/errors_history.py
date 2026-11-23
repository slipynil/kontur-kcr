import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from import_worker.database import ImportErrorDelivery

logger = logging.getLogger(__name__)
HTTP_TIMEOUT = 5
CLAIM_DURATION = timedelta(seconds=30)
MAX_BACKOFF = 60
POLL_INTERVAL = 1


async def send(client: httpx.AsyncClient, payload: str) -> int | None:
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            response = await client.post(
                "/internal/v1/import-errors/bulk",
                content=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if response.status_code == 409:
            return 409
        if response.status_code == 200 and response.json() in (
            {"inserted": 1, "existing": 0},
            {"inserted": 0, "existing": 1},
        ):
            return 200
    except httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError:
        pass
    return None


async def deliver_one(
    sessions: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
) -> bool:
    async with sessions.begin() as db:
        delivery = await db.scalar(
            select(ImportErrorDelivery)
            .where(
                ImportErrorDelivery.next_attempt_at <= func.now(),
                ImportErrorDelivery.delivered_at.is_(None),
                ImportErrorDelivery.rejected_status.is_(None),
            )
            .order_by(ImportErrorDelivery.next_attempt_at, ImportErrorDelivery.source_error_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if delivery is None:
            return False
        delivery.attempt_count += 1
        delivery.next_attempt_at = func.now() + CLAIM_DURATION
        source_id, attempt, payload = (
            delivery.source_error_id,
            delivery.attempt_count,
            delivery.payload,
        )

    # The reservation expires after a crash. A late sender cannot finish a newer attempt.
    status = await send(client, payload)
    if status == 200:
        values = {"delivered_at": func.now()}
    elif status == 409:
        values = {"rejected_status": status}
        logger.error("Errors History отклонил ошибку импорта %s (HTTP 409)", source_id)
    else:
        delay = min(MAX_BACKOFF, 2 ** min(attempt - 1, 6))
        values = {"next_attempt_at": func.now() + timedelta(seconds=delay)}
    async with sessions.begin() as db:
        await db.execute(
            update(ImportErrorDelivery)
            .where(
                ImportErrorDelivery.source_error_id == source_id,
                ImportErrorDelivery.attempt_count == attempt,
            )
            .values(**values)
        )
    return True


async def deliver_errors(
    sessions: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
) -> None:
    while True:
        try:
            if await deliver_one(sessions, client):
                continue
        except SQLAlchemyError as exc:
            logger.warning("Доставка в Errors History будет повторена (%s)", type(exc).__name__)
        await asyncio.sleep(POLL_INTERVAL)
