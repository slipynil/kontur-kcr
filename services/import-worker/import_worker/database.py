from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, any_, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from import_worker.models import Batch, ImportErrorRow, ImportParameters, Status


class Base(DeclarativeBase):
    pass


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    uploaded_by: Mapped[UUID]
    filename: Mapped[str] = mapped_column(Text)
    selected_quarter: Mapped[str] = mapped_column(Text)
    import_type: Mapped[str] = mapped_column(Text)
    snapshot_date: Mapped[date]
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


class ImportObservation(Base):
    __tablename__ = "import_observations"

    fingerprint: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), primary_key=True
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


def add_errors(db: AsyncSession, batch: ImportBatch, errors: list[ImportErrorRow]) -> None:
    db.add_all([ImportError(batch_id=batch.id, **row.model_dump()) for row in errors])
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
