import asyncio
import logging
from datetime import UTC, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from clients_service.import_application import IMPORT_LOCK, ImportNotFound
from clients_service.recovery_application import RecoveryConflict
from clients_service.recovery_models import RollbackConfirmation

logger = logging.getLogger(__name__)
RETENTION = timedelta(days=90)
CHECK_INTERVAL_SECONDS = 3600
DELETE_LIMIT = 1000


def confirm_rollback(engine: Engine, batch_id: UUID) -> RollbackConfirmation:
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK})
        parameters = {"batch_id": batch_id}
        confirmed_at = connection.scalar(
            text("""
                UPDATE clients_import_operations
                SET rollback_confirmed_at = coalesce(rollback_confirmed_at, clock_timestamp())
                WHERE batch_id = :batch_id AND rolled_back
                RETURNING rollback_confirmed_at
            """),
            parameters,
        )
        if confirmed_at is None:
            exists = connection.scalar(
                text("SELECT 1 FROM clients_import_operations WHERE batch_id = :batch_id"),
                parameters,
            )
            if exists is None:
                raise ImportNotFound
            raise RecoveryConflict("IMPORT_ROLLBACK_NOT_APPLIED")
        return RollbackConfirmation(batch_id=batch_id, rollback_confirmed_at=confirmed_at)


def purge_expired_copies(engine: Engine) -> bool:
    """Delete one chunk. Return whether another chunk may be ready."""
    with engine.begin() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK}
        )
        if not acquired:
            return False
        now = connection.scalar(text("SELECT clock_timestamp()"))
        parameters = {"cutoff": now.astimezone(UTC) - RETENTION, "limit": DELETE_LIMIT}
        undo = connection.execute(
            text("""
                WITH expired AS (
                    SELECT u.batch_id, u.kcr_code
                    FROM clients_import_operations o JOIN clients_import_undo u USING (batch_id)
                    WHERE o.rollback_confirmed_at <= :cutoff
                    LIMIT :limit
                )
                DELETE FROM clients_import_undo u USING expired e
                WHERE u.batch_id = e.batch_id AND u.kcr_code = e.kcr_code
            """),
            parameters,
        ).rowcount
        archive = connection.execute(
            text("""
                WITH expired AS (
                    SELECT d.operation_id, d.history_id
                    FROM clients_import_operations o
                    JOIN clients_deleted_history d
                      ON d.payload->>'import_batch_id' = o.batch_id::text
                    WHERE o.rollback_confirmed_at <= :cutoff
                    LIMIT :limit
                )
                DELETE FROM clients_deleted_history d USING expired e
                WHERE d.operation_id = e.operation_id AND d.history_id = e.history_id
            """),
            parameters,
        ).rowcount
        return undo == DELETE_LIMIT or archive == DELETE_LIMIT


async def run_retention(engine: Engine, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if await asyncio.to_thread(purge_expired_copies, engine):
                continue
        except SQLAlchemyError:
            logger.warning("Recovery retention check failed and will be retried")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except TimeoutError:
            continue
