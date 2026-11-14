"""Keep a durable record of completed full client cleanups."""

from alembic import op

revision = "20261109_03"
down_revision = "20261109_02"


def upgrade():
    op.execute(
        """CREATE TABLE client_cleanup_operations (
               operation_id UUID PRIMARY KEY,
               kcr_code TEXT NOT NULL,
               actor_id UUID NOT NULL,
               recorded_at TIMESTAMPTZ NOT NULL
           )"""
    )


def downgrade():
    op.execute("DROP TABLE client_cleanup_operations")
