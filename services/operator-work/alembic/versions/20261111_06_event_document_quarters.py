"""Keep explicit document quarters on operator events."""

from alembic import op

revision = "20261111_06"
down_revision = "20261109_05"


def upgrade():
    op.execute(
        """ALTER TABLE client_events
           ADD COLUMN document_quarters TEXT[] NOT NULL DEFAULT '{}'::text[]"""
    )


def downgrade():
    op.execute("ALTER TABLE client_events DROP COLUMN document_quarters")
