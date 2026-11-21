import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from operator_work.assignments import WorkError, _active_assignment, _lock, _one, _record_stage
from operator_work.contracts import (
    BatchCleanupRequest,
    CleanupBody,
    ClientCleanupRequest,
    QuarterCleanupRequest,
    RestoreCycle,
)


def _fingerprint(body: CleanupBody) -> str | None:
    if isinstance(body, ClientCleanupRequest):
        return None
    payload = body.model_dump(mode="json")
    payload["cycle_ids"].sort()
    payload["event_ids"].sort()
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _record_cleanup(db: Connection, body: CleanupBody, actor: UUID, now: datetime) -> bool:
    params = {
        "operation_id": body.operation_id,
        "kcr_code": body.kcr_code,
        "actor_id": actor,
        "scope": body.scope,
        "fingerprint": _fingerprint(body),
    }
    recorded = _one(
        db,
        """INSERT INTO client_cleanup_operations
               (operation_id, kcr_code, actor_id, scope, fingerprint, recorded_at)
           VALUES (:operation_id, :kcr_code, :actor_id, :scope, :fingerprint, :now)
           ON CONFLICT (operation_id) DO NOTHING RETURNING operation_id""",
        **params,
        now=now,
    )
    if recorded is not None:
        return True
    prior = _one(
        db,
        "SELECT * FROM client_cleanup_operations WHERE operation_id = :id",
        id=body.operation_id,
    )
    if prior is None or any(prior[key] != value for key, value in params.items()):
        raise WorkError(409, "OPERATION_ID_CONFLICT", "ID операции уже используется")
    return False


def _restore(db: Connection, code: str, quarter: str, restore: RestoreCycle, actor: UUID, now):
    params = {
        "code": code,
        "quarter": quarter,
        "cycle": restore.cycle_id,
        "wait": restore.wait_until,
        "source": restore.wait_source_work_quarter,
        "published": restore.published,
        "operator": restore.operator_id,
        "actor": actor,
        "stage": restore.stage,
        "now": now,
    }
    db.execute(
        text("""UPDATE client_work_cycles
                SET is_current = TRUE, wait_until = :wait, wait_source_work_quarter = :source,
                    published_at = CASE WHEN :published THEN COALESCE(published_at, :now) END
                WHERE cycle_id = :cycle"""),
        params,
    )
    active = _active_assignment(db, code, quarter)
    if active is not None and active["operator_id"] != restore.operator_id:
        db.execute(
            text(
                "UPDATE client_assignments SET released_at = :now, released_by = :actor WHERE id = :id"
            ),
            {**params, "id": active["id"]},
        )
    if restore.operator_id is not None and (
        active is None or active["operator_id"] != restore.operator_id
    ):
        db.execute(
            text("""INSERT INTO client_assignments (id, kcr_code, work_quarter, operator_id, assigned_at)
                    VALUES (:id, :code, :quarter, :operator, :now)"""),
            {**params, "id": uuid4()},
        )
    _record_stage(db, code, quarter, actor, restore.stage, now)
    db.execute(
        text("""INSERT INTO client_events
                       (id, kcr_code, work_quarter, author_id, event_type, comment_text, stage,
                        wait_until, created_at)
                VALUES (:id, :code, :quarter, :actor, 'STAGE_CHANGE',
                        'Состояние цикла восстановлено после отката', :stage, :wait, :now)"""),
        {**params, "id": uuid4()},
    )
    db.execute(
        text("""UPDATE client_reassignment_requests SET status = 'CANCELLED', resolved_at = :now
                WHERE kcr_code = :code AND work_quarter = :quarter AND status = 'PENDING'"""),
        params,
    )


class Cleanup:
    def __init__(self, engine: Engine):
        self.engine = engine

    def apply(self, body: CleanupBody, actor: UUID) -> None:
        with self.engine.begin() as db:
            now = _lock(db, body.kcr_code)
            if not _record_cleanup(db, body, actor, now):
                return
            if isinstance(body, ClientCleanupRequest):
                db.execute(
                    text(
                        "DELETE FROM client_events WHERE kcr_code = :code AND work_quarter IS NULL"
                    ),
                    {"code": body.kcr_code},
                )
                db.execute(
                    text("DELETE FROM client_work_cycles WHERE kcr_code = :code"),
                    {"code": body.kcr_code},
                )
                return
            self._apply_targets(db, body, actor, now)

    @staticmethod
    def _apply_targets(db: Connection, body: CleanupBody, actor: UUID, now: datetime):
        cycles = db.execute(
            text("""SELECT cycle_id, work_quarter, is_current, source_import_batch_id
                    FROM client_work_cycles WHERE kcr_code = :code"""),
            {"code": body.kcr_code},
        ).mappings()
        by_id = {cycle["cycle_id"]: cycle for cycle in cycles}
        selected = set(body.cycle_ids)
        if selected - by_id.keys():
            raise WorkError(409, "CLEANUP_TARGET_CONFLICT", "Цикл не найден у этого клиента")
        removed = [by_id[cycle_id] for cycle_id in selected]
        survivors = {
            cycle_id: cycle for cycle_id, cycle in by_id.items() if cycle_id not in selected
        }
        removing_current = any(cycle["is_current"] for cycle in removed)

        restore = None
        if isinstance(body, BatchCleanupRequest):
            if any(cycle["source_import_batch_id"] != body.batch_id for cycle in removed):
                raise WorkError(409, "CLEANUP_TARGET_CONFLICT", "Цикл создан другим пакетом")
            restore = body.restore
            if removing_current and survivors and restore is None:
                raise WorkError(
                    409, "RESTORE_REQUIRED", "Укажите цикл и состояние для восстановления"
                )
            if restore is not None:
                if not removing_current or restore.cycle_id not in survivors:
                    raise WorkError(409, "INVALID_RESTORE", "Нельзя восстановить выбранный цикл")
                source = restore.wait_source_work_quarter
                other_quarters = {
                    cycle["work_quarter"]
                    for cycle_id, cycle in survivors.items()
                    if cycle_id != restore.cycle_id
                }
                if source is not None and source not in other_quarters:
                    raise WorkError(409, "INVALID_RESTORE", "Исходный цикл ожидания недоступен")

        params = {
            "code": body.kcr_code,
            "cycle_ids": body.cycle_ids,
            "event_ids": body.event_ids,
            "quarters": [cycle["work_quarter"] for cycle in removed],
        }
        events = (
            db.execute(
                text("""SELECT id, work_quarter, event_type, document_quarters FROM client_events
                    WHERE kcr_code = :code
                      AND (id = ANY(CAST(:event_ids AS uuid[]))
                           OR work_quarter = ANY(CAST(:quarters AS text[])))"""),
                params,
            )
            .mappings()
            .all()
        )
        if {event["id"] for event in events} != set(body.event_ids):
            raise WorkError(
                409,
                "CLEANUP_TARGET_CONFLICT",
                "Не все события цикла перечислены или цель недоступна",
            )
        for event in events:
            if event["event_type"] in ("OPERATOR_ASSIGN", "STAGE_CHANGE"):
                removable = event["work_quarter"] in params["quarters"]
            else:
                quarters = event["document_quarters"]
                removable = len(quarters) == 1 and (
                    not isinstance(body, QuarterCleanupRequest) or quarters[0] == body.quarter
                )
            if not removable:
                raise WorkError(
                    409,
                    "CLEANUP_TARGET_CONFLICT",
                    "Цель включает общую или неподтверждённую историю",
                )

        db.execute(
            text(
                "DELETE FROM client_events WHERE kcr_code = :code AND id = ANY(CAST(:event_ids AS uuid[]))"
            ),
            params,
        )
        db.execute(
            text("""DELETE FROM client_work_cycles
                    WHERE kcr_code = :code AND cycle_id = ANY(CAST(:cycle_ids AS uuid[]))"""),
            params,
        )
        if restore is not None:
            _restore(
                db, body.kcr_code, survivors[restore.cycle_id]["work_quarter"], restore, actor, now
            )
