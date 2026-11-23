from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from errors_history.models import ErrorBulkResult, ErrorInput, ErrorItem, ErrorPage

events = Table(
    "import_error_events",
    MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("source_error_id", BigInteger, nullable=False, unique=True),
    Column("batch_id", Uuid, nullable=False),
    Column("import_type", Text, nullable=False),
    Column("kcr_code", Text),
    Column("sheet", Text, nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("recorded_at", DateTime(timezone=True)),
)
event_data = tuple(column for column in events.columns if column is not events.c.id)


class SourceErrorConflict(Exception):
    def __init__(self, source_error_id: int) -> None:
        self.source_error_id = source_error_id


def save_errors(engine: Engine, errors: list[ErrorInput]) -> ErrorBulkResult:
    by_id: dict[int, ErrorInput] = {}
    for error in errors:
        if by_id.setdefault(error.source_error_id, error) != error:
            raise SourceErrorConflict(error.source_error_id)

    # A common insert order prevents circular waits between overlapping requests.
    rows = [{"id": uuid4(), **by_id[source_id].model_dump()} for source_id in sorted(by_id)]
    statement = (
        insert(events)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[events.c.source_error_id])
        .returning(events.c.source_error_id)
    )
    with engine.begin() as connection:
        inserted = set(connection.scalars(statement))
        existing = by_id.keys() - inserted
        if existing:
            # Under READ COMMITTED this sees any concurrent insert that won the conflict.
            previous = connection.execute(
                select(*event_data)
                .where(events.c.source_error_id.in_(existing))
                .order_by(events.c.source_error_id)
            ).mappings()
            for row in previous:
                source_id = row["source_error_id"]
                if ErrorInput.model_validate(row) != by_id[source_id]:
                    raise SourceErrorConflict(source_id)
    return ErrorBulkResult(inserted=len(inserted), existing=len(existing))


def list_errors(
    engine: Engine, conditions: list[ColumnElement[bool]], page: int, page_size: int
) -> ErrorPage:
    statement = (
        select(events)
        .where(*conditions)
        .order_by(events.c.recorded_at.desc().nulls_last(), events.c.source_error_id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ErrorPage(
        items=[ErrorItem.model_validate(row) for row in rows[:page_size]],
        page=page,
        page_size=page_size,
        has_more=len(rows) > page_size,
    )
