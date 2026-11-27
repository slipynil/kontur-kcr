"""Record import error timestamps and queue delivery to Errors History."""

from alembic import op

revision = "0006_errors_outbox"
down_revision = "0005_remove_client_blocking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set the default separately so existing errors keep their unknown timestamps.
    op.execute("ALTER TABLE import_errors ADD COLUMN recorded_at TIMESTAMPTZ")
    op.execute("ALTER TABLE import_errors ALTER COLUMN recorded_at SET DEFAULT clock_timestamp()")
    op.execute("""
        CREATE TABLE import_errors_outbox (
            source_error_id BIGINT PRIMARY KEY,
            payload         TEXT NOT NULL,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            attempt_count   INTEGER NOT NULL DEFAULT 0,
            delivered_at    TIMESTAMPTZ,
            rejected_status INTEGER
        )
    """)
    op.execute("""
        CREATE INDEX idx_import_errors_outbox_pending
            ON import_errors_outbox (next_attempt_at, source_error_id)
            WHERE delivered_at IS NULL AND rejected_status IS NULL
    """)


def downgrade() -> None:
    op.drop_table("import_errors_outbox")
    op.drop_column("import_errors", "recorded_at")
