import asyncio
import logging
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractRobustChannel
from pamqp.commands import Basic
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from import_worker.database import ImportBatch
from import_worker.models import Status

QUEUE_NAME = "import.jobs"
QUEUE_ARGUMENTS = {"x-queue-type": "classic"}
RETRY_DELAY = 3
logger = logging.getLogger(__name__)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID


async def publish_one(
    sessions: async_sessionmaker[AsyncSession], channel: AbstractRobustChannel
) -> bool:
    async with sessions.begin() as db:
        batch = await db.scalar(
            select(ImportBatch)
            .where(
                ImportBatch.status == Status.PROCESSING,
                ImportBatch.published_at.is_(None),
                ImportBatch.file_sha256.is_not(None),
            )
            .order_by(ImportBatch.created_at, ImportBatch.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if batch is None:
            return False
        confirmation = await channel.default_exchange.publish(
            aio_pika.Message(
                Job(batch_id=batch.id).model_dump_json().encode(),
                message_id=str(batch.id),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=QUEUE_NAME,
            mandatory=True,
            timeout=10,
        )
        if not isinstance(confirmation, Basic.Ack):
            raise RuntimeError("RabbitMQ не подтвердил публикацию")
        batch.published_at = func.clock_timestamp()
        return True


async def publish_pending(amqp_url: str, sessions: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(amqp_url, timeout=5)
            async with connection:
                channel = await connection.channel(on_return_raises=True)
                await channel.declare_queue(QUEUE_NAME, durable=True, arguments=QUEUE_ARGUMENTS)
                while True:
                    if not await publish_one(sessions, channel):
                        await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning("Публикация импорта будет повторена (%s)", type(exc).__name__)
            await asyncio.sleep(RETRY_DELAY)
