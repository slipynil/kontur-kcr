from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from import_worker.database import (
    ImportBatch,
    ImportRollback,
    RollbackCleanup,
    active_rollback,
    has_newer_clients_apply,
    lock_imports,
    report,
)
from import_worker.models import Batch, ImportType, Status
from import_worker.operator_work import AffectedClient, admin_headers, affected_clients

SYSTEM_EVENTS = {"OPERATOR_ASSIGN", "STAGE_CHANGE"}


class RollbackProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def unavailable() -> RollbackProblem:
    return RollbackProblem(
        503, "IMPORT_ROLLBACK_UNAVAILABLE", "Координация отката между сервисами недоступна"
    )


def not_latest() -> RollbackProblem:
    return RollbackProblem(
        409, "IMPORT_NOT_LATEST", "Можно отменить только последний применённый пакет"
    )


def target_conflict(message: str) -> RollbackProblem:
    return RollbackProblem(409, "CLEANUP_TARGET_CONFLICT", message)


class WorkCycle(BaseModel):
    cycle_id: UUID
    work_quarter: str
    is_current: bool
    published_at: datetime | None
    source_import_batch_id: UUID | None
    assigned_operator_id: UUID | None
    stage: str | None
    wait_until: date | None
    wait_source_work_quarter: str | None


class WorkCycles(BaseModel):
    items: list[WorkCycle]


class WorkEvent(BaseModel):
    id: UUID
    work_quarter: str | None
    event_type: str
    document_quarters: list[str]


class EventPage(BaseModel):
    items: list[WorkEvent]
    total: int


class RestoreCycle(BaseModel):
    cycle_id: UUID
    published: bool
    stage: Literal["POOL", "IN_WORK", "WAIT_DEADLINE"]
    operator_id: UUID | None
    wait_until: date | None
    wait_source_work_quarter: str | None

    @model_validator(mode="after")
    def validate_state(self):
        if self.stage == "WAIT_DEADLINE":
            if self.operator_id is None or self.wait_until is None:
                raise ValueError("WAIT_DEADLINE требует ответственного и дату")
        elif self.wait_until is not None or self.wait_source_work_quarter is not None:
            raise ValueError("Срок задаётся только для WAIT_DEADLINE")
        if (self.stage == "POOL") != (self.operator_id is None):
            raise ValueError("Назначение не соответствует этапу цикла")
        return self


class ClientsRollbackResult(BaseModel):
    batch_id: UUID
    operation_id: UUID
    local_only: Literal[True]


class CleanupResult(BaseModel):
    operation_id: UUID
    kcr_code: str
    scope: Literal["BATCH"]
    status: Literal["COMPLETED"]


class RollbackConfirmation(BaseModel):
    batch_id: UUID
    rollback_confirmed_at: datetime


def restored_state(removed: list[WorkCycle], survivors: list[WorkCycle]) -> dict | None:
    current = next((cycle for cycle in removed if cycle.is_current), None)
    if current is None or not survivors:
        return None
    previous = max(survivors, key=lambda cycle: cycle.work_quarter)
    if current.wait_source_work_quarter is not None:
        previous = next(
            (
                cycle
                for cycle in survivors
                if cycle.work_quarter == current.wait_source_work_quarter
            ),
            None,
        )
    if previous is None or previous.work_quarter >= current.work_quarter:
        raise target_conflict("Предыдущий цикл для восстановления не найден")
    if previous.wait_source_work_quarter and not any(
        cycle.work_quarter == previous.wait_source_work_quarter for cycle in survivors
    ):
        raise target_conflict("Исходный цикл ожидания срока недоступен")
    try:
        return RestoreCycle(
            cycle_id=previous.cycle_id,
            published=previous.published_at is not None,
            stage=previous.stage,
            operator_id=previous.assigned_operator_id,
            wait_until=previous.wait_until,
            wait_source_work_quarter=previous.wait_source_work_quarter,
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise target_conflict("Не удалось определить состояние предыдущего цикла") from exc


async def cleanup_command(
    work: httpx.AsyncClient, affected: AffectedClient, batch_id: UUID, actor: UUID
) -> RollbackCleanup | None:
    path = f"/internal/v1/clients/{quote(affected.kcr_code, safe='')}"
    headers = admin_headers(actor)
    response = await work.get(f"{path}/cycles", headers=headers)
    response.raise_for_status()
    cycles = WorkCycles.model_validate(response.json()).items
    removed = [cycle for cycle in cycles if cycle.source_import_batch_id == batch_id]
    if not removed:
        return None
    if len(removed) > 100:
        raise target_conflict("Пакет затрагивает слишком много циклов одного клиента")
    cycle_ids = {cycle.cycle_id for cycle in removed}
    event_ids: set[UUID] = set()
    for cycle in removed:
        page = 1
        total = None
        while total is None or (page - 1) * 100 < total:
            response = await work.get(
                f"{path}/events",
                headers=headers,
                params={"work_quarter": cycle.work_quarter, "page": page, "page_size": 100},
            )
            response.raise_for_status()
            result = EventPage.model_validate(response.json())
            if (
                result.total < 0
                or (total is not None and result.total != total)
                or len(result.items) != min(100, result.total - (page - 1) * 100)
            ):
                raise target_conflict("Лента клиента изменилась при подготовке отката")
            total = result.total
            for event in result.items:
                if event.id in event_ids or event.work_quarter != cycle.work_quarter:
                    raise target_conflict("Лента клиента изменилась при подготовке отката")
                if event.event_type not in SYSTEM_EVENTS and (
                    len(event.document_quarters) != 1
                    or event.document_quarters[0] not in affected.source_quarters
                ):
                    raise target_conflict("В цикле есть история, которую требуется сохранить")
                event_ids.add(event.id)
            if len(event_ids) > 5000:
                raise target_conflict("Пакет затрагивает слишком много событий одного клиента")
            page += 1
    restore = restored_state(
        removed, [cycle for cycle in cycles if cycle.cycle_id not in cycle_ids]
    )
    payload = {
        "operation_id": str(uuid4()),
        "scope": "BATCH",
        "batch_id": str(batch_id),
        "kcr_code": affected.kcr_code,
        "cycle_ids": sorted(map(str, cycle_ids)),
        "event_ids": sorted(map(str, event_ids)),
    }
    if restore is not None:
        payload["restore"] = restore
    return RollbackCleanup(batch_id=batch_id, kcr_code=affected.kcr_code, payload=payload)


async def prepare_rollback(
    db: AsyncSession,
    batch_id: UUID,
    actor: UUID | None,
    operation_id: UUID | None,
    clients: httpx.AsyncClient | None,
    work: httpx.AsyncClient | None,
) -> Batch | None:
    await lock_imports(db)
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise RollbackProblem(404, "IMPORT_NOT_FOUND", "Пакет не найден")
    existing = await db.get(ImportRollback, batch_id)
    if operation_id is not None:
        keyed = await db.scalar(
            select(ImportRollback).where(ImportRollback.operation_id == operation_id)
        )
        if keyed is not None and (keyed.batch_id != batch_id or keyed.requested_by != actor):
            raise RollbackProblem(409, "OPERATION_ID_CONFLICT", "ID операции уже используется")
    if existing is not None:
        if existing.requested_by != actor or (
            operation_id is not None and existing.operation_id != operation_id
        ):
            raise RollbackProblem(
                409, "OPERATION_ID_CONFLICT", "Повторите исходную операцию отката"
            )
        if existing.rejected_code == "IMPORT_NOT_LATEST":
            raise not_latest()
        if existing.completed_at is not None:
            return await report(db, batch)
        if clients is None:
            raise unavailable()
        return None
    if (
        batch.clients_applied_at is None
        or batch.status in {Status.FAILED, Status.ROLLED_BACK}
        or await has_newer_clients_apply(db, batch)
    ):
        raise not_latest()
    if batch.status != Status.COMPLETED or clients is None:
        raise unavailable()
    if actor is None:
        raise RollbackProblem(400, "INVALID_IMPORT_REQUEST", "Требуется инициатор отката")
    if (
        await active_rollback(db) is not None
        or await db.scalar(
            select(ImportBatch.id)
            .where(
                ImportBatch.status == Status.PROCESSING,
                or_(
                    ImportBatch.apply_started_at.is_not(None),
                    ImportBatch.clients_applied_at.is_not(None),
                ),
            )
            .limit(1)
        )
        is not None
    ):
        raise unavailable()
    cleanups = []
    if batch.import_type == ImportType.SNAPSHOT:
        if work is None:
            raise unavailable()
        for affected in await affected_clients(clients, batch_id):
            command = await cleanup_command(work, affected, batch_id, actor)
            if command is not None:
                cleanups.append(command)
    db.add(
        ImportRollback(batch_id=batch_id, operation_id=operation_id or uuid4(), requested_by=actor)
    )
    await db.flush()
    db.add_all(cleanups)
    return None


def check_status(response: httpx.Response) -> None:
    if response.status_code == 409:
        body = response.json()
        if not isinstance(body, dict):
            raise unavailable()
        if body.get("code") == "IMPORT_NOT_LATEST":
            raise not_latest()
        raise RollbackProblem(
            409,
            body.get("code", "CLEANUP_TARGET_CONFLICT"),
            body.get("message", "План отката требует сверки"),
        )
    response.raise_for_status()


async def execute_rollback(
    sessions: async_sessionmaker[AsyncSession],
    batch_id: UUID,
    clients: httpx.AsyncClient,
    work: httpx.AsyncClient | None,
) -> Batch:
    while True:
        async with sessions.begin() as db:
            await lock_imports(db)
            operation = await db.get(ImportRollback, batch_id)
            if operation.rejected_code == "IMPORT_NOT_LATEST":
                raise not_latest()
            if operation.completed_at is not None:
                return await report(db, await db.get(ImportBatch, batch_id))
            if operation.clients_confirmed_at is None:
                response = await clients.post(
                    f"/internal/v1/import/{batch_id}/rollback",
                    json={
                        "operation_id": str(operation.operation_id),
                        "requested_by": str(operation.requested_by),
                    },
                )
                try:
                    check_status(response)
                except RollbackProblem as exc:
                    if exc.code != "IMPORT_NOT_LATEST":
                        raise
                    operation.rejected_code = exc.code
                    operation.completed_at = func.clock_timestamp()
                    continue
                result = ClientsRollbackResult.model_validate(response.json())
                if result.batch_id != batch_id or result.operation_id != operation.operation_id:
                    raise unavailable()
                operation.clients_confirmed_at = func.clock_timestamp()
                continue
            cleanup = await db.scalar(
                select(RollbackCleanup)
                .where(
                    RollbackCleanup.batch_id == batch_id,
                    RollbackCleanup.confirmed_at.is_(None),
                )
                .order_by(RollbackCleanup.kcr_code)
                .limit(1)
            )
            if cleanup is not None:
                if work is None:
                    raise unavailable()
                response = await work.post(
                    "/internal/v1/data-cleanup",
                    headers=admin_headers(operation.requested_by),
                    json=cleanup.payload,
                )
                check_status(response)
                result = CleanupResult.model_validate(response.json())
                if (
                    str(result.operation_id) != cleanup.payload["operation_id"]
                    or result.kcr_code != cleanup.kcr_code
                ):
                    raise unavailable()
                cleanup.confirmed_at = func.clock_timestamp()
                continue
            response = await clients.put(f"/internal/v1/import/{batch_id}/rollback-confirmation")
            check_status(response)
            confirmation = RollbackConfirmation.model_validate(response.json())
            if confirmation.batch_id != batch_id:
                raise unavailable()
            operation.completed_at = confirmation.rollback_confirmed_at
            batch = await db.get(ImportBatch, batch_id)
            batch.status = Status.ROLLED_BACK
            return await report(db, batch)
