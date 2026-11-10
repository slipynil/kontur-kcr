"""Remember which snapshots have already updated Operator Work cycles."""

from alembic import op

revision = "20261109_04"
down_revision = "20261109_03"


def upgrade():
    op.execute(
        """CREATE TABLE applied_work_snapshots (
               batch_id UUID PRIMARY KEY,
               fingerprint TEXT NOT NULL
           )"""
    )


def downgrade():
    op.execute("DROP TABLE applied_work_snapshots")
