"""Store call request identity for replay and assignment validation."""

from alembic import op

revision = "20261109_02"
down_revision = "20261109_01"


def upgrade():
    op.execute("ALTER TABLE client_events ADD COLUMN call_assignment_id UUID")
    op.execute("ALTER TABLE client_events ADD COLUMN requested_wait_until DATE")
    op.execute(
        """ALTER TABLE client_events ADD CONSTRAINT ck_call_result_present
           CHECK (event_type <> 'CALL_RESULT' OR call_result IS NOT NULL)"""
    )


def downgrade():
    op.execute("ALTER TABLE client_events DROP CONSTRAINT ck_call_result_present")
    op.execute("ALTER TABLE client_events DROP COLUMN requested_wait_until")
    op.execute("ALTER TABLE client_events DROP COLUMN call_assignment_id")
