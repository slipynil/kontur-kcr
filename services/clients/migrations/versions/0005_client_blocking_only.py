"""Replace client status with the existing blocking flag."""

from alembic import op

revision = "0005_client_blocking_only"
down_revision = "0004_rollback_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE clients
        SET field_sources = field_sources - 'status',
            inherited_fields = inherited_fields - 'status',
            needs_review = CASE WHEN inherited_fields ? 'status'
                THEN (inherited_fields - 'status') <> '{}'::jsonb ELSE needs_review END
        WHERE field_sources ? 'status' OR inherited_fields ? 'status';

        UPDATE quarter_snapshot_history
        SET inherited_fields = inherited_fields - 'status'
        WHERE inherited_fields ? 'status';

        ALTER TABLE clients DROP COLUMN status;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE clients ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'
            CHECK (status IN ('ACTIVE', 'INACTIVE'));
    """)
