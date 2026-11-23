"""Create the import error history."""

from alembic import op

revision = "0001_errors_history"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = """\
CREATE TABLE import_error_events (
    id              UUID        PRIMARY KEY,
    source_error_id BIGINT      NOT NULL UNIQUE CHECK (source_error_id > 0),
    batch_id        UUID        NOT NULL,
    import_type     TEXT        NOT NULL CHECK (import_type IN ('REGISTRY', 'SNAPSHOT')),
    kcr_code        TEXT,
    sheet           TEXT        NOT NULL,
    row_number      INTEGER     NOT NULL CHECK (row_number >= 0),
    reason          TEXT        NOT NULL,
    recorded_at     TIMESTAMPTZ
);

CREATE INDEX idx_import_error_events_kcr
    ON import_error_events (kcr_code, recorded_at DESC NULLS LAST, source_error_id DESC);
CREATE INDEX idx_import_error_events_batch
    ON import_error_events (batch_id, recorded_at DESC NULLS LAST, source_error_id DESC);
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.drop_table("import_error_events")
