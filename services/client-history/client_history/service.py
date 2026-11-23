from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from client_history.database import HistoryCleanupRow, HistoryEventRow, row_values, to_event
from client_history.errors import HistoryError, unavailable
from client_history.models import (
    CleanupScope,
    HistoryCleanupRequest,
    HistoryCleanupResult,
    HistoryEventBase,
    HistoryEventBatch,
    HistoryEventListResponse,
    IngestResult,
    Source,
    fingerprint,
)
from client_history.upstreams import Names, Registry

# Another sender may insert the same id first; the attempt is then read again.
ATTEMPTS = 5


class _ConcurrentWrite(Exception):
    pass


class History:
    def __init__(
        self, engine: Engine, names: Names, registry: Registry, attempts: int = ATTEMPTS
    ) -> None:
        self.engine = engine
        self.names = names
        self.registry = registry
        self.attempts = attempts

    def ingest(self, batch: HistoryEventBatch) -> IngestResult:
        """Saves every new record of the batch or none of them."""
        prepared: dict[UUID, tuple[HistoryEventBase, str]] = {}
        for event in batch.events:
            if event.id in prepared:
                raise HistoryError(
                    422, "VALIDATION_ERROR", f"Запись {event.id} повторяется в пакете"
                )
            prepared[event.id] = (event, fingerprint(event))
        for _ in range(self.attempts):
            pending = self._pending(prepared)
            if pending:
                self._name_authors(pending)
                if not self._insert(batch.source, pending):
                    continue
            return IngestResult(accepted=len(pending), duplicates=len(prepared) - len(pending))
        raise unavailable("Не удалось сохранить записи. Повторите отправку")

    def _pending(
        self, prepared: dict[UUID, tuple[HistoryEventBase, str]]
    ) -> list[tuple[HistoryEventBase, str]]:
        with self.engine.connect() as connection:
            stored = dict(
                connection.execute(
                    select(HistoryEventRow.id, HistoryEventRow.fingerprint).where(
                        HistoryEventRow.id.in_(list(prepared))
                    )
                ).all()
            )
        pending = []
        for event_id, (event, digest) in prepared.items():
            known = stored.get(event_id)
            if known is not None and known != digest:
                raise HistoryError(
                    409,
                    "HISTORY_EVENT_CONFLICT",
                    f"Запись {event_id} уже сохранена с иным содержимым",
                )
            if known is None:
                pending.append((event, digest))
        return pending

    def _name_authors(self, pending: list[tuple[HistoryEventBase, str]]) -> None:
        unnamed = {
            event.actor_id
            for event, _ in pending
            if event.actor_id is not None and event.actor_name is None
        }
        names = self.names.resolve(unnamed)
        for event, _ in pending:
            if event.actor_name is None and event.actor_id in names:
                event.actor_name = names[event.actor_id]

    def _insert(self, source: Source, pending: list[tuple[HistoryEventBase, str]]) -> bool:
        """False when a concurrent sender inserted one of the records first."""
        statement = (
            insert(HistoryEventRow)
            .values([row_values(source, event, digest) for event, digest in pending])
            .on_conflict_do_nothing(index_elements=[HistoryEventRow.id])
            .returning(HistoryEventRow.id)
        )
        try:
            with self.engine.begin() as connection:
                if len(connection.scalars(statement).all()) != len(pending):
                    raise _ConcurrentWrite
        except _ConcurrentWrite:
            return False
        return True

    def cleanup(self, command: HistoryCleanupRequest) -> HistoryCleanupResult:
        """Deletes once per operation_id; a repeat returns the first result."""
        digest = command.fingerprint()
        with self.engine.begin() as connection:
            fresh = connection.scalar(
                insert(HistoryCleanupRow)
                .values(
                    operation_id=command.operation_id,
                    kcr_code=command.kcr_code,
                    fingerprint=digest,
                    deleted=0,
                )
                .on_conflict_do_nothing(index_elements=[HistoryCleanupRow.operation_id])
                .returning(HistoryCleanupRow.operation_id)
            )
            if fresh is None:
                prior = connection.execute(
                    select(HistoryCleanupRow.fingerprint, HistoryCleanupRow.deleted).where(
                        HistoryCleanupRow.operation_id == command.operation_id
                    )
                ).one_or_none()
                if prior is None or prior.fingerprint != digest:
                    raise HistoryError(409, "OPERATION_ID_CONFLICT", "ID операции уже используется")
                deleted = prior.deleted
            else:
                deleted = self._delete(connection, command)
                connection.execute(
                    update(HistoryCleanupRow)
                    .where(HistoryCleanupRow.operation_id == command.operation_id)
                    .values(deleted=deleted)
                )
        return HistoryCleanupResult(
            operation_id=command.operation_id,
            kcr_code=command.kcr_code,
            scope=command.scope,
            deleted=deleted,
        )

    @staticmethod
    def _delete(connection: Connection, command: HistoryCleanupRequest) -> int:
        if command.scope is CleanupScope.CLIENT:
            statement = delete(HistoryEventRow).where(HistoryEventRow.kcr_code == command.kcr_code)
        else:
            targets = []
            if command.event_ids:
                targets.append(HistoryEventRow.id.in_(command.event_ids))
            if command.cycle_ids:
                targets.append(HistoryEventRow.cycle_id.in_(command.cycle_ids))
            statement = delete(HistoryEventRow).where(
                HistoryEventRow.kcr_code == command.kcr_code, or_(*targets)
            )
        return connection.execute(statement).rowcount

    def list(
        self, code: str, quarter: str | None, page: int, size: int
    ) -> HistoryEventListResponse:
        """One page of the chronicle, newest first by the date the event happened."""
        conditions = [HistoryEventRow.kcr_code == code]
        if quarter is not None:
            conditions.append(HistoryEventRow.work_quarter == quarter)
        with Session(self.engine) as session:
            total = session.scalar(
                select(func.count()).select_from(HistoryEventRow).where(*conditions)
            )
            rows = (
                session.scalars(
                    select(HistoryEventRow)
                    .where(*conditions)
                    .order_by(HistoryEventRow.occurred_at.desc(), HistoryEventRow.seq.desc())
                    .limit(size)
                    .offset((page - 1) * size)
                ).all()
                if total
                else []
            )
        # Only an absent code is a 404; a registry client without history is an empty page.
        if not total and self.registry.exists(code) is False:
            raise HistoryError(404, "CLIENT_NOT_FOUND", "Клиент не найден")
        return HistoryEventListResponse(
            items=[to_event(row) for row in rows], total=total, page=page, page_size=size
        )
