"""Local import result, undo, field origins and stable observation ordering."""

from alembic import op

revision = "0002_import_apply"
down_revision = "0001_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE clients ADD COLUMN field_sources JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("""
        ALTER TABLE quarter_snapshot_history
        ADD COLUMN observation_order BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
        ADD COLUMN inherited_fields JSONB NOT NULL DEFAULT '{}'::jsonb;
        CREATE INDEX idx_history_observation ON quarter_snapshot_history
            (kcr_code, quarter, snapshot_date DESC, observation_order DESC);
        CREATE TABLE clients_import_operations (
            batch_id UUID PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            result JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE clients_import_undo (
            batch_id UUID NOT NULL REFERENCES clients_import_operations(batch_id) ON DELETE CASCADE,
            kcr_code TEXT NOT NULL,
            previous_state JSONB NOT NULL,
            created_history_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            PRIMARY KEY (batch_id, kcr_code)
        );
    """)


def downgrade() -> None:
    op.drop_table("clients_import_undo")
    op.drop_table("clients_import_operations")
    op.drop_index("idx_history_observation", table_name="quarter_snapshot_history")
    op.drop_column("quarter_snapshot_history", "inherited_fields")
    op.drop_column("quarter_snapshot_history", "observation_order")
    op.drop_column("clients", "field_sources")
