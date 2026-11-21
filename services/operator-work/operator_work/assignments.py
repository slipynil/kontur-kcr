import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from operator_work.contracts import SnapshotDelivery

BUSINESS_ZONE = ZoneInfo("Europe/Moscow")


class WorkError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _one(db: Connection, sql: str, **params) -> RowMapping | None:
    return db.execute(text(sql), params).mappings().one_or_none()


def _lock(db: Connection, code: str) -> datetime:
    # Lock by client across quarters, including when the first cycle does not yet exist.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:code, 0))"), {"code": code})
    return db.scalar(text("SELECT clock_timestamp()"))


def _current_cycle(db: Connection, code: str) -> RowMapping | None:
    return _one(
        db,
        """SELECT work_quarter, published_at, wait_until, wait_source_work_quarter
           FROM client_work_cycles
           WHERE kcr_code = :code AND is_current""",
        code=code,
    )


def _latest_quarter(db: Connection, code: str) -> str | None:
    return db.scalar(
        text("SELECT max(work_quarter) FROM client_work_cycles WHERE kcr_code = :code"),
        {"code": code},
    )


def _active_assignment(db: Connection, code: str, quarter: str) -> RowMapping | None:
    return _one(
        db,
        """SELECT id, operator_id, assigned_at FROM client_assignments
           WHERE kcr_code = :code AND work_quarter = :quarter AND released_at IS NULL""",
        code=code,
        quarter=quarter,
    )


def _wait_expired(cycle: RowMapping, now: datetime) -> bool:
    return (
        cycle["wait_until"] is not None
        and now.astimezone(BUSINESS_ZONE).date() >= cycle["wait_until"]
    )


def _record_stage(db, code, quarter, actor, stage, now):
    db.execute(
        text(
            """INSERT INTO client_stages
                   (id, kcr_code, work_quarter, stage, changed_by, changed_at)
               VALUES (:id, :code, :quarter, :stage, :actor, :now)"""
        ),
        {
            "id": uuid4(),
            "code": code,
            "quarter": quarter,
            "stage": stage,
            "actor": actor,
            "now": now,
        },
    )


def _assign(
    db: Connection,
    code: str,
    quarter: str,
    operator: UUID,
    previous: RowMapping | None,
    now: datetime,
) -> dict:
    assignment_id = uuid4()
    params = {
        "id": assignment_id,
        "code": code,
        "quarter": quarter,
        "operator": operator,
        "now": now,
    }
    if previous is not None:
        db.execute(
            text(
                """UPDATE client_assignments SET released_at = :now, released_by = :operator
                   WHERE id = :previous_id"""
            ),
            {**params, "previous_id": previous["id"]},
        )
    else:
        _record_stage(db, code, quarter, operator, "IN_WORK", now)
    db.execute(
        text(
            """INSERT INTO client_assignments (id, kcr_code, work_quarter, operator_id, assigned_at)
               VALUES (:id, :code, :quarter, :operator, :now)"""
        ),
        params,
    )
    db.execute(
        text(
            """INSERT INTO client_events
                   (id, kcr_code, work_quarter, author_id, event_type, comment_text, created_at)
               VALUES (:event_id, :code, :quarter, :operator, 'OPERATOR_ASSIGN', :comment, :now)"""
        ),
        {
            **params,
            "event_id": uuid4(),
            "comment": "Клиент перезакреплён" if previous else "Клиент взят в работу",
        },
    )
    return _assignment_result(code, quarter, assignment_id, operator, now)


def _assignment_result(code, quarter, assignment_id, operator, assigned_at) -> dict:
    return {
        "kcr_code": code,
        "work_quarter": quarter,
        "assignment_id": assignment_id,
        "assigned_operator_id": operator,
        "assigned_at": assigned_at,
    }


def _require_cycle(db: Connection, code: str, quarter: str) -> RowMapping:
    cycle = _current_cycle(db, code)
    if cycle is not None and cycle["work_quarter"] != quarter:
        raise WorkError(409, "INVALID_STATE", "Цикл обзвона изменился")
    if cycle is None or cycle["published_at"] is None:
        raise WorkError(404, "CLIENT_NOT_PUBLISHED", "Клиент не опубликован в пуле")
    return cycle


def _state(db: Connection, code: str, quarter: str, now: datetime) -> dict:
    cycle = _require_cycle(db, code, quarter)
    assignment = _active_assignment(db, code, quarter)
    stage = db.scalar(
        text("""SELECT stage FROM client_stages
                WHERE kcr_code = :code AND work_quarter = :quarter
                ORDER BY changed_at DESC, id DESC LIMIT 1"""),
        {"code": code, "quarter": quarter},
    )
    result = _assignment_result(
        code,
        quarter,
        assignment["id"] if assignment else None,
        assignment["operator_id"] if assignment else None,
        assignment["assigned_at"] if assignment else None,
    )
    return {
        **result,
        "stage": stage,
        "wait_until": cycle["wait_until"],
        "wait_source_work_quarter": cycle["wait_source_work_quarter"],
        "awaiting_balance": _wait_expired(cycle, now),
    }


def _record_snapshot_part(
    db: Connection,
    batch_id: UUID,
    quarter: str,
    signature: str,
    codes: list[str],
    delivery: SnapshotDelivery,
) -> bool:
    params = {
        "batch": batch_id,
        "quarter": quarter,
        "id": delivery.delivery_id,
        "number": delivery.part_number,
        "count": delivery.part_count,
        "fingerprint": signature,
        "codes": codes,
    }
    db.execute(
        text("""INSERT INTO applied_work_snapshots (batch_id, work_quarter, part_count)
                VALUES (:batch, :quarter, :count) ON CONFLICT (batch_id) DO NOTHING"""),
        params,
    )
    batch = _one(
        db,
        """SELECT work_quarter, part_count FROM applied_work_snapshots
           WHERE batch_id = :batch FOR UPDATE""",
        batch=batch_id,
    )
    if (
        batch is None
        or batch["work_quarter"] != quarter
        or batch["part_count"] != delivery.part_count
    ):
        raise WorkError(
            409, "SNAPSHOT_ID_CONFLICT", "ID пакета уже использован с другими параметрами"
        )

    previous = _one(
        db,
        """SELECT part_number, fingerprint FROM applied_work_snapshot_parts
           WHERE batch_id = :batch AND delivery_id = :id""",
        batch=batch_id,
        id=delivery.delivery_id,
    )
    if previous is not None:
        if previous["part_number"] != delivery.part_number or previous["fingerprint"] != signature:
            raise WorkError(409, "SNAPSHOT_PART_CONFLICT", "ID доставки уже использован")
        return False
    overlap = db.scalar(
        text("""SELECT EXISTS (
                    SELECT 1 FROM applied_work_snapshot_parts WHERE batch_id = :batch
                      AND client_codes && CAST(:codes AS text[])
                )"""),
        params,
    )
    if overlap:
        raise WorkError(409, "SNAPSHOT_PART_CONFLICT", "Клиент уже входит в другую часть пакета")
    recorded = _one(
        db,
        """INSERT INTO applied_work_snapshot_parts
               (batch_id, delivery_id, part_number, fingerprint, client_codes)
           VALUES (:batch, :id, :number, :fingerprint, CAST(:codes AS text[]))
           ON CONFLICT DO NOTHING RETURNING delivery_id""",
        **params,
    )
    if recorded is None:
        raise WorkError(409, "SNAPSHOT_PART_CONFLICT", "Номер части уже использован")
    return True


def _record_snapshot(
    db: Connection,
    batch_id: UUID,
    quarter: str,
    clients: list[tuple[str, int]],
    delivery: SnapshotDelivery | None,
) -> bool:
    signature = sha256(json.dumps([quarter, clients]).encode()).hexdigest()
    if delivery is not None:
        return _record_snapshot_part(
            db, batch_id, quarter, signature, [code for code, _ in clients], delivery
        )
    recorded = _one(
        db,
        """INSERT INTO applied_work_snapshots (batch_id, fingerprint)
           VALUES (:id, :fingerprint)
           ON CONFLICT (batch_id) DO NOTHING RETURNING batch_id""",
        id=batch_id,
        fingerprint=signature,
    )
    if recorded is not None:
        return True
    previous = db.scalar(
        text("SELECT fingerprint FROM applied_work_snapshots WHERE batch_id = :id"),
        {"id": batch_id},
    )
    if previous != signature:
        raise WorkError(409, "SNAPSHOT_ID_CONFLICT", "ID пакета уже использован")
    return False


class Assignments:
    def __init__(self, engine: Engine):
        self.engine = engine

    def publish(self, code: str, quarter: str, admin: UUID) -> None:
        self.publish_batch([code], quarter, admin)

    def publish_batch(self, codes: list[str], quarter: str, admin: UUID) -> None:
        with self.engine.begin() as db:
            # Overlapping lists take client locks in the same order.
            for code in sorted(set(codes)):
                self._publish(db, code, quarter, admin)

    @staticmethod
    def _publish(db: Connection, code: str, quarter: str, admin: UUID) -> None:
        now = _lock(db, code)
        cycle = _current_cycle(db, code)
        if cycle is None or cycle["work_quarter"] != quarter:
            latest = _latest_quarter(db, code)
            if latest is not None and latest >= quarter:
                raise WorkError(
                    409, "INVALID_STATE", f"{code}: исторический цикл нельзя опубликовать"
                )
            Assignments._create_cycle(db, code, quarter, admin, now, cycle)
        db.execute(
            text("""UPDATE client_work_cycles SET published_at = :now
                    WHERE kcr_code = :code AND work_quarter = :quarter AND published_at IS NULL"""),
            {"code": code, "quarter": quarter, "now": now},
        )

    def apply_snapshot(
        self,
        batch_id: UUID,
        quarter: str,
        balances: dict[str, int],
        actor: UUID,
        delivery: SnapshotDelivery | None = None,
    ) -> None:
        clients = sorted(balances.items())
        with self.engine.begin() as db:
            if not _record_snapshot(db, batch_id, quarter, clients, delivery):
                return

            for code, outstanding in clients:
                if outstanding == 0:
                    continue
                now = _lock(db, code)
                latest = _latest_quarter(db, code)
                if latest is not None and latest >= quarter:
                    continue
                cycle = _current_cycle(db, code)
                self._create_cycle(db, code, quarter, actor, now, cycle, batch_id)

    @staticmethod
    def _create_cycle(
        db: Connection,
        code: str,
        quarter: str,
        actor: UUID,
        now: datetime,
        cycle: RowMapping | None,
        batch_id: UUID | None = None,
    ) -> None:
        previous_quarter = cycle["work_quarter"] if cycle else None
        previous = (
            _active_assignment(db, code, previous_quarter)
            if cycle is not None and cycle["wait_until"] is not None
            else None
        )
        params = {"code": code, "quarter": quarter, "now": now}
        if cycle is not None:
            db.execute(
                text(
                    "UPDATE client_work_cycles SET is_current = FALSE WHERE kcr_code = :code AND is_current"
                ),
                params,
            )
        db.execute(
            text(
                """INSERT INTO client_work_cycles
                       (kcr_code, work_quarter, wait_until, wait_source_work_quarter,
                        source_import_batch_id, is_current)
                   VALUES (:code, :quarter, :wait_until, :source, :batch_id, TRUE)"""
            ),
            {
                **params,
                "wait_until": cycle["wait_until"] if previous else None,
                "source": previous_quarter if previous else None,
                "batch_id": batch_id,
            },
        )
        if previous:
            db.execute(
                text(
                    """INSERT INTO client_assignments
                           (id, kcr_code, work_quarter, operator_id, assigned_at)
                       VALUES (:id, :code, :quarter, :operator, :now)"""
                ),
                {**params, "id": uuid4(), "operator": previous["operator_id"]},
            )
        stage = "WAIT_DEADLINE" if previous else "POOL"
        _record_stage(db, code, quarter, actor, stage, now)

    def cycles(self, code: str) -> dict:
        with self.engine.connect() as db:
            rows = db.execute(
                text("""SELECT cycle.cycle_id, cycle.work_quarter, cycle.is_current,
                               cycle.published_at, cycle.source_import_batch_id,
                               assignment.operator_id AS assigned_operator_id,
                               latest.stage, cycle.wait_until, cycle.wait_source_work_quarter
                        FROM client_work_cycles AS cycle
                        LEFT JOIN client_assignments AS assignment
                          ON assignment.kcr_code = cycle.kcr_code
                         AND assignment.work_quarter = cycle.work_quarter
                         AND assignment.released_at IS NULL
                        LEFT JOIN LATERAL (
                            SELECT stage FROM client_stages
                            WHERE kcr_code = cycle.kcr_code AND work_quarter = cycle.work_quarter
                            ORDER BY changed_at DESC, id DESC LIMIT 1
                        ) AS latest ON TRUE
                        WHERE cycle.kcr_code = :code ORDER BY cycle.work_quarter DESC"""),
                {"code": code},
            ).mappings()
            return {"items": [dict(row) for row in rows]}

    def published_codes(self, code: str | None = None) -> list[str]:
        with self.engine.connect() as db:
            return list(
                db.execute(
                    text("""SELECT kcr_code FROM client_work_cycles
                        WHERE is_current AND published_at IS NOT NULL
                          AND (CAST(:code AS text) IS NULL OR kcr_code = :code)"""),
                    {"code": code},
                ).scalars()
            )

    def current_assignees(self, codes: list[str]) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                text("""SELECT cycle.kcr_code, assignment.operator_id AS assigned_operator_id
                        FROM client_work_cycles AS cycle
                        JOIN client_assignments AS assignment
                          ON assignment.kcr_code = cycle.kcr_code
                         AND assignment.work_quarter = cycle.work_quarter
                         AND assignment.released_at IS NULL
                        WHERE cycle.is_current AND cycle.published_at IS NOT NULL
                          AND cycle.kcr_code = ANY(CAST(:codes AS text[]))"""),
                {"codes": codes},
            ).mappings()
            return [dict(row) for row in rows]

    def pool(self, quarter: str, visible_codes: list[str] | None = None) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """SELECT cycle.kcr_code, assignment.operator_id AS assigned_operator_id
                       FROM client_work_cycles AS cycle
                       LEFT JOIN client_assignments AS assignment
                         ON assignment.kcr_code = cycle.kcr_code
                        AND assignment.work_quarter = cycle.work_quarter
                        AND assignment.released_at IS NULL
                       WHERE cycle.work_quarter = :quarter AND cycle.published_at IS NOT NULL
                         AND (cycle.wait_until IS NULL OR
                              cycle.wait_until > (statement_timestamp() AT TIME ZONE
                                                  'Europe/Moscow')::date)
                         AND cycle.is_current
                         AND (CAST(:codes AS text[]) IS NULL
                              OR cycle.kcr_code = ANY(CAST(:codes AS text[])))
                       ORDER BY cycle.kcr_code"""
                ),
                {"quarter": quarter, "codes": visible_codes},
            ).mappings()
            return [dict(row) for row in rows]

    def state(self, code: str, quarter: str) -> dict:
        with self.engine.begin() as db:
            now = _lock(db, code)
            return _state(db, code, quarter, now)

    def state_current(self, code: str, quarter: str | None = None) -> dict:
        """Work state for the public client card: the requested cycle or the
        current one (docs/api/internal/operator-work-service.yaml readClientWorkState)."""
        with self.engine.begin() as db:
            now = _lock(db, code)
            if quarter is None:
                current = _current_cycle(db, code)
                if current is None:
                    raise WorkError(
                        404, "WORK_CYCLE_NOT_FOUND", "Действующий цикл обзвона не найден"
                    )
                quarter = current["work_quarter"]
            return _state(db, code, quarter, now)

    def take(
        self, code: str, quarter: str, operator: UUID, expected: UUID | None, request_id: UUID
    ) -> dict:
        with self.engine.begin() as db:
            now = _lock(db, code)
            existing = _one(
                db,
                """SELECT a.id AS assignment_id, a.kcr_code, a.work_quarter,
                          a.operator_id AS assigned_operator_id, a.assigned_at,
                          op.expected_assignment_id
                   FROM client_take_operations AS op
                   JOIN client_assignments AS a ON a.id = op.assignment_id
                   WHERE op.id = :id""",
                id=request_id,
            )
            if existing is not None:
                if (
                    existing["assigned_operator_id"] != operator
                    or existing["kcr_code"] != code
                    or existing["work_quarter"] != quarter
                    or existing["expected_assignment_id"] != expected
                ):
                    raise WorkError(409, "REQUEST_ID_CONFLICT", "ID операции уже используется")
                return _assignment_result(
                    code, quarter, existing["assignment_id"], operator, existing["assigned_at"]
                )

            cycle = _require_cycle(db, code, quarter)
            active = _active_assignment(db, code, quarter)
            own = active is not None and active["operator_id"] == operator
            if not own and expected != (active["id"] if active else None):
                raise WorkError(
                    409, "ASSIGNMENT_CHANGED", "Ответственный изменился. Обновите карточку"
                )
            if _wait_expired(cycle, now):
                db.execute(
                    text("""UPDATE client_work_cycles
                            SET wait_until = NULL, wait_source_work_quarter = NULL
                            WHERE kcr_code = :code AND work_quarter = :quarter"""),
                    {"code": code, "quarter": quarter},
                )
                if active is not None:
                    _record_stage(db, code, quarter, operator, "IN_WORK", now)
            if own:
                result = _assignment_result(
                    code, quarter, active["id"], operator, active["assigned_at"]
                )
            else:
                result = _assign(db, code, quarter, operator, active, now)
            recorded = _one(
                db,
                """INSERT INTO client_take_operations (id, assignment_id, expected_assignment_id)
                   VALUES (:id, :assignment, :expected)
                   ON CONFLICT (id) DO NOTHING RETURNING id""",
                id=request_id,
                assignment=result["assignment_id"],
                expected=expected,
            )
            if recorded is None:
                raise WorkError(409, "REQUEST_ID_CONFLICT", "ID операции уже используется")
            return result
