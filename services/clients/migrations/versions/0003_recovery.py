"""Local rollback state, command results and deleted-history undo."""

from alembic import op

revision = "0003_recovery"
down_revision = "0002_import_apply"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE SEQUENCE clients_change_order_seq;
        ALTER TABLE clients_import_operations
            ADD COLUMN apply_order BIGINT UNIQUE,
            ADD COLUMN rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN rollback_result JSONB;
        ALTER TABLE clients_import_operations ALTER COLUMN apply_order
            SET DEFAULT nextval('clients_change_order_seq');
        CREATE TABLE clients_data_operations (
            operation_id UUID PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('ROLLBACK','DELETE_CLIENT','DELETE_QUARTER')),
            kcr_code TEXT,
            quarter TEXT,
            batch_id UUID,
            requested_by UUID NOT NULL,
            change_order BIGINT NOT NULL UNIQUE DEFAULT nextval('clients_change_order_seq'),
            result JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE clients_deleted_history (
            operation_id UUID NOT NULL REFERENCES clients_data_operations(operation_id)
                ON DELETE CASCADE,
            history_id UUID NOT NULL,
            kcr_code TEXT NOT NULL,
            quarter TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (operation_id, history_id)
        );
        CREATE INDEX idx_deleted_history_client ON clients_deleted_history(kcr_code);
    """)


def downgrade() -> None:
    op.drop_table("clients_deleted_history")
    op.drop_table("clients_data_operations")
    op.drop_column("clients_import_operations", "rollback_result")
    op.drop_column("clients_import_operations", "rolled_back")
    op.drop_column("clients_import_operations", "apply_order")
    op.execute("DROP SEQUENCE clients_change_order_seq")
