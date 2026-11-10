import logging
from collections.abc import Callable
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, text

from operator_work.assignments import _lock, _one, _record_stage

logger = logging.getLogger(__name__)
SYSTEM_ACTOR_ID = UUID(int=0)

# A stage ID also distinguishes a renewed wait with the same date and assignment.
DUE_WAITS = """
    SELECT cycle.kcr_code, cycle.work_quarter, cycle.wait_until,
           assignment.id AS assignment_id, latest.id AS stage_id
    FROM client_work_cycles AS cycle
    JOIN client_assignments AS assignment USING (kcr_code, work_quarter)
    JOIN LATERAL (
        SELECT id, stage FROM client_stages
        WHERE kcr_code = cycle.kcr_code AND work_quarter = cycle.work_quarter
        ORDER BY changed_at DESC, id DESC LIMIT 1
    ) AS latest ON latest.stage = 'WAIT_DEADLINE'
    WHERE cycle.published_at IS NOT NULL
      AND cycle.wait_until <= (statement_timestamp() AT TIME ZONE 'Europe/Moscow')::date
      AND assignment.released_at IS NULL
      AND cycle.is_current
"""


def outstanding_documents(client: httpx.Client, code: str) -> int | None:
    try:
        response = client.get("/internal/v1/clients/" + quote(code, safe=""))
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError, ValueError:
        logger.debug("Could not confirm outstanding documents in Clients", exc_info=True)
        return None
    if not isinstance(data, dict) or data.get("kcr_code") != code:
        return None
    amount = data.get("outstanding_documents")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        return None
    return amount


class WaitExpiry:
    def __init__(self, engine: Engine, clients: httpx.Client):
        self.engine = engine
        self.clients = clients

    def process(self, stopped: Callable[[], bool] | None = None) -> None:
        with self.engine.connect() as db:
            due = db.execute(text(DUE_WAITS + " ORDER BY cycle.kcr_code")).mappings().all()

        for wait in due:
            if stopped is not None and stopped():
                return
            code = wait["kcr_code"]
            amount = outstanding_documents(self.clients, code)
            if amount is None or amount == 0:
                continue

            with self.engine.begin() as db:
                now = _lock(db, code)
                # The external read runs outside the transaction and can race with a new wait.
                current = _one(db, DUE_WAITS + " AND cycle.kcr_code = :code", code=code)
                if current != wait:
                    continue
                quarter = wait["work_quarter"]
                params = {
                    "code": code,
                    "quarter": quarter,
                    "assignment": wait["assignment_id"],
                    "event": uuid4(),
                    "actor": SYSTEM_ACTOR_ID,
                    "now": now,
                }
                db.execute(
                    text("""UPDATE client_assignments SET released_at = :now
                            WHERE id = :assignment"""),
                    params,
                )
                db.execute(
                    text("""UPDATE client_work_cycles
                            SET wait_until = NULL, wait_source_work_quarter = NULL
                            WHERE kcr_code = :code AND work_quarter = :quarter"""),
                    params,
                )
                _record_stage(db, code, quarter, SYSTEM_ACTOR_ID, "POOL", now)
                db.execute(
                    text("""INSERT INTO client_events
                               (id, kcr_code, work_quarter, author_id, event_type,
                                comment_text, stage, created_at)
                            VALUES (:event, :code, :quarter, :actor, 'STAGE_CHANGE',
                                    'Срок ожидания истёк. Клиент возвращён в пул', 'POOL', :now)"""),
                    params,
                )
