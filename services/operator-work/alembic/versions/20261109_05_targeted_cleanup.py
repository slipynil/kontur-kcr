"""Track current cycles explicitly and record targeted cleanup requests."""

from alembic import op

revision = "20261109_05"
down_revision = "20261109_04"


def upgrade():
    op.execute(
        "ALTER TABLE client_work_cycles ADD COLUMN cycle_id UUID NOT NULL DEFAULT gen_random_uuid()"
    )
    op.execute("ALTER TABLE client_work_cycles ADD CONSTRAINT uq_work_cycle_id UNIQUE (cycle_id)")
    op.execute(
        "ALTER TABLE client_work_cycles ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        """UPDATE client_work_cycles AS cycle SET is_current = TRUE
           WHERE NOT EXISTS (
               SELECT 1 FROM client_work_cycles AS newer
               WHERE newer.kcr_code = cycle.kcr_code AND newer.work_quarter > cycle.work_quarter
           )"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_current_work_cycle
           ON client_work_cycles (kcr_code) WHERE is_current"""
    )
    op.execute(
        """ALTER TABLE client_cleanup_operations
           ADD COLUMN scope TEXT NOT NULL DEFAULT 'CLIENT'
               CHECK (scope IN ('CLIENT', 'QUARTER', 'BATCH')),
           ADD COLUMN fingerprint TEXT"""
    )


def downgrade():
    op.execute("ALTER TABLE client_cleanup_operations DROP COLUMN fingerprint, DROP COLUMN scope")
    op.execute("DROP INDEX uq_current_work_cycle")
    op.execute("ALTER TABLE client_work_cycles DROP COLUMN is_current, DROP COLUMN cycle_id")
