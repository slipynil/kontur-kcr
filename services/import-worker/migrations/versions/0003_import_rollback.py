from alembic import op

revision = "0003_import_rollback"
down_revision = "0002_operator_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE import_rollbacks (
            batch_id UUID PRIMARY KEY REFERENCES import_batches (id) ON DELETE CASCADE,
            operation_id UUID NOT NULL UNIQUE,
            requested_by UUID NOT NULL,
            clients_confirmed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            rejected_code TEXT
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_import_active_rollback ON import_rollbacks ((TRUE))
        WHERE completed_at IS NULL
    """)
    op.execute("""
        CREATE TABLE import_rollback_cleanups (
            batch_id UUID NOT NULL REFERENCES import_rollbacks (batch_id) ON DELETE CASCADE,
            kcr_code TEXT NOT NULL,
            payload JSONB NOT NULL,
            confirmed_at TIMESTAMPTZ,
            PRIMARY KEY (batch_id, kcr_code)
        )
    """)


def downgrade() -> None:
    op.drop_table("import_rollback_cleanups")
    op.drop_table("import_rollbacks")
