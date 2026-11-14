import json
from datetime import datetime, timedelta
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
        """SELECT work_quarter, published_at, wait_until FROM client_work_cycles
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


def _pending(db: Connection, code: str, quarter: str) -> RowMapping | None:
    return _one(
        db,
        """SELECT * FROM client_reassignment_requests
           WHERE kcr_code = :code AND work_quarter = :quarter AND status = 'PENDING'""",
        code=code,
        quarter=quarter,
    )


def _protected(cycle: RowMapping, now: datetime) -> bool:
    return (
        cycle["wait_until"] is not None
        and now.astimezone(BUSINESS_ZONE).date() < cycle["wait_until"]
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


def _resolve(db: Connection, request_id: UUID, status: str, now: datetime) -> RowMapping:
    return (
        db.execute(
            text(
                """UPDATE client_reassignment_requests
                   SET status = :status, resolved_at = :now WHERE id = :id RETURNING *"""
            ),
            {"id": request_id, "status": status, "now": now},
        )
        .mappings()
        .one()
    )


def _settle(db: Connection, request: RowMapping, now: datetime) -> RowMapping:
    code, quarter = request["kcr_code"], request["work_quarter"]
    cycle = _current_cycle(db, code)
    assignment = _active_assignment(db, code, quarter)
    if (
        cycle is None
        or cycle["work_quarter"] != quarter
        or cycle["published_at"] is None
        or assignment is None
        or assignment["id"] != request["expected_assignment_id"]
        or cycle["wait_until"] is not None
    ):
        return _resolve(db, request["id"], "CANCELLED", now)
    _assign(db, code, quarter, request["requester_id"], assignment, now)
    return _resolve(db, request["id"], "TRANSFERRED", now)


def _require_cycle(db: Connection, code: str, quarter: str) -> RowMapping:
    cycle = _current_cycle(db, code)
    if cycle is not None and cycle["work_quarter"] != quarter:
        raise WorkError(409, "INVALID_STATE", "Цикл обзвона изменился")
    if cycle is None or cycle["published_at"] is None:
        raise WorkError(404, "CLIENT_NOT_PUBLISHED", "Клиент не опубликован в пуле")
    return cycle


def _settle_overdue(db: Connection, code: str, quarter: str, now: datetime) -> None:
    pending = _pending(db, code, quarter)
    if pending and pending["expires_at"] <= now:
        _settle(db, pending, now)


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
        db.execute(
            text(
                """UPDATE client_reassignment_requests SET status = 'CANCELLED', resolved_at = :now
                   WHERE kcr_code = :code AND work_quarter < :quarter AND status = 'PENDING'"""
            ),
            params,
        )

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

    def pool(self, quarter: str) -> list[dict]:
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
                       ORDER BY cycle.kcr_code"""
                ),
                {"quarter": quarter},
            ).mappings()
            return [dict(row) for row in rows]

    def state(self, code: str, quarter: str) -> dict:
        with self.engine.begin() as db:
            now = _lock(db, code)
            cycle = _require_cycle(db, code, quarter)
            _settle_overdue(db, code, quarter, now)
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
                "awaiting_balance": cycle["wait_until"] is not None and not _protected(cycle, now),
            }

    def take(
        self, code: str, quarter: str, operator: UUID, expected: UUID | None, request_id: UUID
    ) -> tuple[int, dict]:
        with self.engine.begin() as db:
            now = _lock(db, code)
            existing = _one(
                db, "SELECT * FROM client_reassignment_requests WHERE id = :id", id=request_id
            )
            if existing:
                if (
                    existing["requester_id"] != operator
                    or existing["kcr_code"] != code
                    or existing["work_quarter"] != quarter
                    or existing["expected_assignment_id"] != expected
                ):
                    raise WorkError(409, "REQUEST_ID_CONFLICT", "ID запроса уже используется")
                if existing["status"] == "PENDING" and existing["expires_at"] <= now:
                    existing = _settle(db, existing, now)
                return (202 if existing["status"] == "PENDING" else 200), dict(existing)

            cycle = _require_cycle(db, code, quarter)
            pending = _pending(db, code, quarter)
            if pending and pending["expires_at"] <= now:
                _settle(db, pending, now)
                pending = None
            active = _active_assignment(db, code, quarter)
            if cycle["wait_until"] is not None and not _protected(cycle, now):
                raise WorkError(
                    409,
                    "CLIENT_BALANCE_UNCONFIRMED",
                    "Срок истёк. Возврат в пул зависит от остатка",
                )
            if active and active["operator_id"] == operator:
                return 200, _assignment_result(
                    code, quarter, active["id"], operator, active["assigned_at"]
                )
            if (active["id"] if active else None) != expected:
                raise WorkError(
                    409, "ASSIGNMENT_CHANGED", "Ответственный изменился. Обновите карточку"
                )
            if active and _protected(cycle, now):
                raise WorkError(409, "CLIENT_WAIT_DEADLINE_ACTIVE", "Срок ожидания ещё действует")
            if active is None:
                return 200, _assign(db, code, quarter, operator, None, now)
            if pending:
                raise WorkError(
                    409, "REASSIGNMENT_PENDING", "Запрос перезакрепления уже ожидает ответа"
                )

            row = _one(
                db,
                """INSERT INTO client_reassignment_requests
                       (id, kcr_code, work_quarter, expected_assignment_id, owner_id,
                        requester_id, created_at, expires_at)
                   VALUES (:id, :code, :quarter, :expected, :owner, :requester, :now, :expires_at)
                   ON CONFLICT (id) DO NOTHING RETURNING *""",
                id=request_id,
                code=code,
                quarter=quarter,
                expected=expected,
                owner=active["operator_id"],
                requester=operator,
                now=now,
                expires_at=now + timedelta(hours=1),
            )
            if row is None:
                raise WorkError(409, "REQUEST_ID_CONFLICT", "ID запроса уже используется")
            return 202, dict(row)

    def notifications(self, operator: UUID, limit: int, offset: int) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """SELECT * FROM client_reassignment_requests
                       WHERE owner_id = :operator OR requester_id = :operator
                       ORDER BY (status = 'PENDING') DESC, created_at DESC, id DESC
                       LIMIT :limit OFFSET :offset"""
                ),
                {"operator": operator, "limit": limit, "offset": offset},
            ).mappings()
            return [dict(row) for row in rows]

    def reject(self, request_id: UUID, operator: UUID) -> tuple[int, dict]:
        with self.engine.begin() as db:
            request = self._owned_request(db, request_id, operator)
            now = _lock(db, request["kcr_code"])
            request = self._owned_request(db, request_id, operator)
            if request["status"] == "PENDING":
                if now < request["expires_at"]:
                    request = _resolve(db, request_id, "REJECTED", now)
                else:
                    request = _settle(db, request, now)
            if request["status"] == "REJECTED":
                return 200, dict(request)
            return 409, {"code": "REASSIGNMENT_RESOLVED", "message": "Срок отказа уже истёк"}

    @staticmethod
    def _owned_request(db: Connection, request_id: UUID, operator: UUID) -> RowMapping:
        row = _one(
            db,
            "SELECT * FROM client_reassignment_requests WHERE id = :id AND owner_id = :operator",
            id=request_id,
            operator=operator,
        )
        if row is None:
            raise WorkError(404, "REASSIGNMENT_NOT_FOUND", "Запрос не найден")
        return row

    def settle_due(self) -> None:
        with self.engine.connect() as db:
            due = db.execute(
                text(
                    """SELECT id, kcr_code FROM client_reassignment_requests
                       WHERE status = 'PENDING' AND expires_at <= clock_timestamp()
                       ORDER BY expires_at, id LIMIT 100"""
                )
            ).all()
        for request_id, code in due:
            with self.engine.begin() as db:
                now = _lock(db, code)
                request = _one(
                    db, "SELECT * FROM client_reassignment_requests WHERE id = :id", id=request_id
                )
                if request and request["status"] == "PENDING" and request["expires_at"] <= now:
                    _settle(db, request, now)
