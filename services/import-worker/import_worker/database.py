import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, any_, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from import_worker.models import Batch, ImportErrorRow, ImportParameters, Status

APPLY_LOCK = 0x494D504F5254


class Base(DeclarativeBase):
    pass


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    uploaded_by: Mapped[UUID]
    filename: Mapped[str] = mapped_column(Text)
    selected_quarter: Mapped[str | None] = mapped_column(Text)
    import_type: Mapped[str] = mapped_column(Text)
    snapshot_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(Text, server_default=Status.PROCESSING)
    total_rows: Mapped[int] = mapped_column(server_default=text("0"))
    success_count: Mapped[int] = mapped_column(server_default=text("0"))
    error_count: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_sha256: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_payload: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    apply_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clients_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportError(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    sheet: Mapped[str] = mapped_column(Text)
    row_number: Mapped[int]
    kcr_code: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class ImportErrorDelivery(Base):
    __tablename__ = "import_errors_outbox"

    source_error_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    payload: Mapped[str] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempt_count: Mapped[int] = mapped_column(server_default=text("0"))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_status: Mapped[int | None]


class ImportObservation(Base):
    __tablename__ = "import_observations"

    fingerprint: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), primary_key=True
    )


class OperatorDelivery(Base):
    __tablename__ = "import_operator_deliveries"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), primary_key=True
    )
    part_number: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_status: Mapped[int | None]
    retry_count: Mapped[int] = mapped_column(server_default=text("0"))


class ImportRollback(Base):
    __tablename__ = "import_rollbacks"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    requested_by: Mapped[UUID]
    clients_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_code: Mapped[str | None] = mapped_column(Text)


class RollbackCleanup(Base):
    __tablename__ = "import_rollback_cleanups"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_rollbacks.batch_id", ondelete="CASCADE"), primary_key=True
    )
    kcr_code: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def lock_imports(db: AsyncSession) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPLY_LOCK})


async def active_rollback(db: AsyncSession) -> UUID | None:
    return await db.scalar(
        select(ImportRollback.batch_id).where(ImportRollback.completed_at.is_(None))
    )


async def insert_batch(
    db: AsyncSession,
    batch_id: UUID,
    user_id: UUID,
    filename: str,
    digest: str,
    params: ImportParameters,
) -> Batch:
    batch = ImportBatch(
        id=batch_id,
        uploaded_by=user_id,
        filename=filename,
        file_sha256=digest,
        **params.model_dump(),
    )
    db.add(batch)
    await db.flush()
    return Batch.model_validate(batch, from_attributes=True)


async def report(db: AsyncSession, batch: ImportBatch) -> Batch:
    errors = await db.scalars(
        select(ImportError).where(ImportError.batch_id == batch.id).order_by(ImportError.id)
    )
    result = Batch.model_validate(batch, from_attributes=True)
    result.error_log = [ImportErrorRow.model_validate(row, from_attributes=True) for row in errors]
    return result


async def add_errors(db: AsyncSession, batch: ImportBatch, errors: list[ImportErrorRow]) -> None:
    if not errors:
        return
    rows = [ImportError(batch_id=batch.id, **error.model_dump()) for error in errors]
    db.add_all(rows)
    await db.flush()

    deliveries = []
    for row, error in zip(rows, errors, strict=True):
        assert row.recorded_at is not None
        event = {
            "source_error_id": row.id,
            "batch_id": str(batch.id),
            "import_type": batch.import_type,
            **error.model_dump(mode="json"),
            "recorded_at": row.recorded_at.isoformat(),
        }
        payload = json.dumps({"errors": [event]}, ensure_ascii=False, separators=(",", ":"))
        deliveries.append(ImportErrorDelivery(source_error_id=row.id, payload=payload))
    db.add_all(deliveries)
    batch.error_count += len(errors)


async def applied_fingerprints(db: AsyncSession, fingerprints: list[str]) -> set[str]:
    query = (
        select(ImportObservation.fingerprint)
        .join(ImportBatch, ImportBatch.id == ImportObservation.batch_id)
        .where(
            ImportObservation.fingerprint == any_(bindparam("keys", type_=ARRAY(Text))),
            ImportBatch.status != Status.ROLLED_BACK,
        )
    )
    return set(await db.scalars(query, {"keys": fingerprints}))


async def has_newer_clients_apply(db: AsyncSession, batch: ImportBatch) -> bool:
    if batch.clients_applied_at is None:
        return False
    return bool(
        await db.scalar(
            select(ImportBatch.id)
            .where(
                ImportBatch.clients_applied_at > batch.clients_applied_at,
                ImportBatch.status != Status.ROLLED_BACK,
            )
            .limit(1)
        )
    )
