"""Keep observation metadata only for snapshot batches."""

from alembic import op

revision = "0004_snapshot_metadata"
down_revision = "0003_import_rollback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE import_batches IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM import_batches WHERE import_type = 'REGISTRY'
                  AND status = 'PROCESSING' AND apply_started_at IS NOT NULL
                  AND clients_applied_at IS NULL AND apply_payload IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'Finish started registry deliveries before removing their metadata';
            END IF;
        END $$;
    """)
    op.execute("""
        ALTER TABLE import_batches
            ALTER COLUMN selected_quarter DROP NOT NULL,
            ALTER COLUMN snapshot_date DROP NOT NULL;
    """)
    op.execute("""
        UPDATE import_batches SET selected_quarter = NULL, snapshot_date = NULL
        WHERE import_type = 'REGISTRY';
    """)
    op.execute("""
        ALTER TABLE import_batches ADD CONSTRAINT ck_import_batches_snapshot_metadata CHECK (
            (import_type = 'SNAPSHOT'
                AND selected_quarter IS NOT NULL AND snapshot_date IS NOT NULL)
            OR (import_type = 'REGISTRY' AND selected_quarter IS NULL AND snapshot_date IS NULL)
        );
    """)


def downgrade() -> None:
    # Setting NOT NULL fails atomically if registry batches have lost their legacy values.
    op.execute("""
        ALTER TABLE import_batches
            DROP CONSTRAINT ck_import_batches_snapshot_metadata,
            ALTER COLUMN selected_quarter SET NOT NULL,
            ALTER COLUMN snapshot_date SET NOT NULL;
    """)
