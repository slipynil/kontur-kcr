"""Start recovery retention after the coordinator confirms rollback."""

from alembic import op

revision = "0004_rollback_confirmation"
down_revision = "0003_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE clients_import_operations
            ADD COLUMN rollback_confirmed_at TIMESTAMPTZ,
            ADD CONSTRAINT clients_import_confirmed_rollback
                CHECK (rollback_confirmed_at IS NULL OR rolled_back);
        CREATE INDEX idx_import_rollback_confirmed
            ON clients_import_operations(rollback_confirmed_at)
            WHERE rollback_confirmed_at IS NOT NULL;
        CREATE INDEX idx_deleted_history_batch
            ON clients_deleted_history((payload->>'import_batch_id'));
    """)


def downgrade() -> None:
    op.drop_index("idx_deleted_history_batch", table_name="clients_deleted_history")
    op.drop_index("idx_import_rollback_confirmed", table_name="clients_import_operations")
    op.drop_constraint("clients_import_confirmed_rollback", "clients_import_operations")
    op.drop_column("clients_import_operations", "rollback_confirmed_at")
