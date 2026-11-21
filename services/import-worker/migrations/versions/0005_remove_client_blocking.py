"""Remove client blocking from saved import payloads."""

from alembic import op

revision = "0005_remove_client_blocking"
down_revision = "0004_snapshot_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE import_batches IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM import_batches
                WHERE status = 'PROCESSING' AND apply_started_at IS NOT NULL
                    AND clients_applied_at IS NULL
                    AND jsonb_path_exists(apply_payload, '$.rows[*].item.client.is_blocked')
            ) THEN
                RAISE EXCEPTION 'Finish started client deliveries before removing blocking';
            END IF;
        END $$;
    """)
    op.execute("""
        UPDATE import_batches
        SET apply_payload = jsonb_set(apply_payload, '{rows}', (
            SELECT jsonb_agg(row_data #- '{item,client,is_blocked}' ORDER BY position)
            FROM jsonb_array_elements(apply_payload->'rows')
                WITH ORDINALITY AS prepared(row_data, position)
        ))
        WHERE jsonb_path_exists(apply_payload, '$.rows[*].item.client.is_blocked');
    """)


def downgrade() -> None:
    # Prepared rows without the removed field remain valid in the previous version.
    pass
