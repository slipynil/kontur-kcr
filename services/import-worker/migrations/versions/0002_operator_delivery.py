from alembic import op

revision = "0002_operator_delivery"
down_revision = "0001_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE import_operator_deliveries (
            batch_id UUID NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
            part_number INTEGER NOT NULL CHECK (part_number BETWEEN 1 AND 1000),
            payload JSONB NOT NULL,
            confirmed_at TIMESTAMPTZ,
            rejected_status INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (batch_id, part_number)
        )
    """)
    op.execute("""
        UPDATE import_batches
        SET status = 'COMPLETED', completed_at = clock_timestamp(),
            apply_payload = NULL, apply_started_at = NULL
        WHERE status = 'PROCESSING' AND clients_applied_at IS NOT NULL
          AND import_type = 'REGISTRY'
    """)
    op.execute("""
        UPDATE import_batches SET published_at = NULL, apply_started_at = NULL
        WHERE status = 'PROCESSING' AND clients_applied_at IS NOT NULL
    """)
    op.execute("DROP INDEX uq_import_batches_active_apply")
    op.execute("""
        CREATE UNIQUE INDEX uq_import_batches_active_apply ON import_batches ((TRUE))
        WHERE status = 'PROCESSING' AND apply_started_at IS NOT NULL
    """)
    op.execute("DROP INDEX idx_import_batches_unpublished")
    op.execute("""
        CREATE INDEX idx_import_batches_unpublished ON import_batches (created_at)
        WHERE published_at IS NULL AND status = 'PROCESSING'
          AND (file_sha256 IS NOT NULL OR clients_applied_at IS NOT NULL)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX idx_import_batches_unpublished")
    op.execute("""
        CREATE INDEX idx_import_batches_unpublished ON import_batches (created_at)
        WHERE published_at IS NULL AND file_sha256 IS NOT NULL AND status = 'PROCESSING'
    """)
    op.execute("DROP INDEX uq_import_batches_active_apply")
    op.execute("""
        CREATE UNIQUE INDEX uq_import_batches_active_apply ON import_batches ((TRUE))
        WHERE apply_started_at IS NOT NULL AND apply_payload IS NOT NULL
    """)
    op.drop_table("import_operator_deliveries")
