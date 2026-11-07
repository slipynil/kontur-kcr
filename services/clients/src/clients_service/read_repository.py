"""Read-only, consistent PostgreSQL projections. No operator-owned data."""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine

from clients_service.read_models import ClientCard, ClientPage, HistoryPage
from clients_service.read_rules import comparison_quarters


class ClientNotFound(Exception):
    pass


# A packet/date is one observation of a client, regardless of its row order.
# Deltas preserve balances of quarters absent from an observation. Every zero
# total starts a new episode, so historical delays cannot label a new episode.
DURATION_CTES = """
    deltas AS (
        SELECT h.*,
            h.unsubmitted_documents - lag(h.unsubmitted_documents, 1, 0) OVER (
                PARTITION BY h.kcr_code, h.quarter
                ORDER BY h.snapshot_date, h.observation_order
            ) AS delta,
            substring(h.quarter, 1, 4)::int * 4 + right(h.quarter, 1)::int AS original_q,
            substring(h.observation_quarter, 1, 4)::int * 4
                + right(h.observation_quarter, 1)::int AS observed_q
        FROM quarter_snapshot_history h JOIN candidates c USING (kcr_code)
    ), events AS (
        SELECT kcr_code, import_batch_id, snapshot_date,
            max(observation_order) AS event_order, sum(delta) AS delta,
            min(original_q) FILTER (WHERE unsubmitted_documents > 0) AS original_q,
            max(observed_q) AS observed_q
        FROM deltas GROUP BY kcr_code, import_batch_id, snapshot_date
    ), balances AS (
        SELECT *, sum(delta) OVER (
            PARTITION BY kcr_code ORDER BY snapshot_date, event_order
            ROWS UNBOUNDED PRECEDING
        ) AS remaining
        FROM events
    ), episodes AS (
        SELECT *, sum(CASE WHEN remaining = 0 THEN 1 ELSE 0 END) OVER (
            PARTITION BY kcr_code ORDER BY snapshot_date, event_order
            ROWS UNBOUNDED PRECEDING
        ) AS episode
        FROM balances
    ), streaks AS (
        SELECT *,
            count(*) FILTER (WHERE remaining > 0) OVER (PARTITION BY kcr_code, episode) AS seen,
            max(observed_q) OVER (PARTITION BY kcr_code, episode) AS last_q,
            CASE WHEN episode = 0 THEN
                min(original_q) OVER (PARTITION BY kcr_code, episode)
            ELSE min(observed_q) FILTER (WHERE remaining > 0)
                OVER (PARTITION BY kcr_code, episode)
            END AS first_q,
            row_number() OVER (
                PARTITION BY kcr_code ORDER BY snapshot_date DESC, event_order DESC
            ) AS recency
        FROM episodes
    ), durations AS (
        SELECT kcr_code, CASE
            WHEN remaining = 0 THEN NULL
            WHEN last_q - first_q >= 1 THEN 'LONG_DELAY_2Q'
            WHEN seen > 1 THEN 'REPEATED'
            ELSE 'FIRST_TIME'
        END AS duration FROM streaks WHERE recency = 1
    )
"""


@contextmanager
def read_transaction(engine: Engine):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            yield connection


CANDIDATES_SQL = """
    candidates AS (
        SELECT c.* FROM clients c
        WHERE (CAST(:code AS text) IS NULL OR c.kcr_code = :code)
          AND (CAST(:search AS text) IS NULL
            OR strpos(lower(c.kcr_code), lower(:search)) > 0
            OR strpos(lower(coalesce(c.inn, '')), lower(:search)) > 0)
          AND (SELECT count(DISTINCT t.code) FROM client_tags ct
                JOIN tags t ON t.id = ct.tag_id
                WHERE ct.kcr_code = c.kcr_code AND t.code = ANY(CAST(:tags AS text[]))
              ) = :tag_count
    )
"""

METRICS_SQL = """
    metrics AS (
        SELECT c.kcr_code, c.inn, c.kpp, c.organization_name, c.status, c.is_blocked,
            c.manager_name, c.contacts, c.needs_review, c.inherited_fields,
            ARRAY(SELECT t.code FROM client_tags ct JOIN tags t ON t.id = ct.tag_id
                  WHERE ct.kcr_code = c.kcr_code ORDER BY t.code) AS tags,
            coalesce(totals.remaining, 0) AS outstanding_documents,
            CASE WHEN coalesce(totals.remaining, 0) = 0 THEN NULL ELSE d.duration END AS duration,
            CAST(:quarter AS text) AS dynamics_quarter,
            CAST(:previous_quarter AS text) AS dynamics_previous_quarter,
            current_q.unsubmitted_documents AS dynamics_current_documents,
            previous_q.unsubmitted_documents AS dynamics_previous_documents,
            CASE
                WHEN current_q.unsubmitted_documents IS NULL
                    OR previous_q.unsubmitted_documents IS NULL THEN NULL
                WHEN current_q.unsubmitted_documents = 0 THEN 'ALL_SUBMITTED'
                WHEN current_q.unsubmitted_documents < previous_q.unsubmitted_documents
                    THEN 'REDUCING'
                WHEN current_q.unsubmitted_documents > previous_q.unsubmitted_documents
                    THEN 'GROWING'
                ELSE 'NO_CHANGE'
            END AS dynamics,
            recent.snapshot AS latest_snapshot
        FROM candidates c
        LEFT JOIN durations d USING (kcr_code)
        LEFT JOIN LATERAL (
            SELECT sum(q.unsubmitted_documents) AS remaining
            FROM quarter_snapshots q WHERE q.kcr_code = c.kcr_code
        ) totals ON true
        LEFT JOIN quarter_snapshots current_q
            ON current_q.kcr_code = c.kcr_code AND current_q.quarter = :quarter
        LEFT JOIN quarter_snapshots previous_q
            ON previous_q.kcr_code = c.kcr_code AND previous_q.quarter = :previous_quarter
        LEFT JOIN LATERAL (
            SELECT jsonb_build_object(
                'kcr_code', q.kcr_code, 'quarter', q.quarter,
                'last_observation_quarter', q.last_observation_quarter,
                'total_documents', q.total_documents,
                'unsubmitted_documents', q.unsubmitted_documents,
                'import_batch_id', q.import_batch_id, 'snapshot_date', q.snapshot_date
            ) AS snapshot FROM quarter_snapshots q
            WHERE q.kcr_code = c.kcr_code ORDER BY q.quarter DESC LIMIT 1
        ) recent ON true
    )
"""
READ_CTES = "WITH " + CANDIDATES_SQL + ", " + DURATION_CTES + ", " + METRICS_SQL


def _parameters(*, quarter=None, code=None, search=None, tags=()):
    quarter, previous = comparison_quarters(quarter)
    tags = sorted(set(tags))
    return {
        "quarter": quarter,
        "previous_quarter": previous,
        "code": code,
        "search": search,
        "tags": tags,
        "tag_count": len(tags),
    }


def list_clients(
    engine: Engine,
    *,
    quarter=None,
    search=None,
    tags=(),
    dynamics=None,
    duration=None,
    page=1,
    page_size=20,
):
    parameters = _parameters(quarter=quarter, search=search, tags=tags)
    parameters.update(
        dynamics=dynamics, duration=duration, limit=page_size, offset=(page - 1) * page_size
    )
    predicate = """(CAST(:dynamics AS text) IS NULL OR dynamics = :dynamics)
        AND (CAST(:duration AS text) IS NULL OR duration = :duration)"""
    with read_transaction(engine) as connection:
        total = connection.scalar(
            text(READ_CTES + " SELECT count(*) FROM metrics WHERE " + predicate), parameters
        )
        rows = (
            connection.execute(
                text(
                    READ_CTES
                    + " SELECT * FROM metrics WHERE "
                    + predicate
                    + ' ORDER BY kcr_code COLLATE "C" LIMIT :limit OFFSET :offset'
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        return ClientPage(items=rows, total=total, page=page, page_size=page_size)


def get_card(engine: Engine, code: str, *, quarter: str | None = None, snapshots_limit: int = 8):
    parameters = _parameters(quarter=quarter, code=code)
    with read_transaction(engine) as connection:
        result = (
            connection.execute(text(READ_CTES + " SELECT * FROM metrics"), parameters)
            .mappings()
            .first()
        )
        if result is None:
            raise ClientNotFound
        profile = dict(result)
        profile["snapshots"] = list(
            connection.execute(
                text("""
            SELECT kcr_code, quarter, last_observation_quarter, total_documents,
                unsubmitted_documents, import_batch_id, snapshot_date
            FROM quarter_snapshots WHERE kcr_code = :code
            ORDER BY quarter DESC LIMIT :limit
        """),
                {"code": code, "limit": snapshots_limit},
            ).mappings()
        )
        return ClientCard.model_validate(profile)


def get_history(engine: Engine, code: str, *, quarter=None, limit=20, offset=0):
    with read_transaction(engine) as connection:
        if (
            connection.scalar(text("SELECT 1 FROM clients WHERE kcr_code = :code"), {"code": code})
            is None
        ):
            raise ClientNotFound
        predicate = "kcr_code = :code AND (CAST(:quarter AS text) IS NULL OR quarter = :quarter)"
        parameters = {"code": code, "quarter": quarter, "limit": limit, "offset": offset}
        total = connection.scalar(
            text("SELECT count(*) FROM quarter_snapshot_history WHERE " + predicate), parameters
        )
        rows = (
            connection.execute(
                text(
                    """
            SELECT id, kcr_code, quarter, observation_quarter, unsubmitted_documents,
                import_batch_id, snapshot_date, created_at, observation_order, inherited_fields
            FROM quarter_snapshot_history WHERE
        """
                    + predicate
                    + " ORDER BY snapshot_date DESC, observation_order DESC"
                    + " LIMIT :limit OFFSET :offset"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        return HistoryPage(items=rows, total=total, limit=limit, offset=offset)
