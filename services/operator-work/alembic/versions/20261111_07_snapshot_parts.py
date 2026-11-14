"""Record independent snapshot delivery parts under the original batch ID."""

from alembic import op

revision = "20261111_07"
down_revision = "20261111_06"


def upgrade():
    op.execute(
        """ALTER TABLE applied_work_snapshots
               ALTER COLUMN fingerprint DROP NOT NULL,
               ADD COLUMN work_quarter TEXT,
               ADD COLUMN part_count INTEGER,
               ADD CONSTRAINT ck_work_snapshot_delivery_mode CHECK (
                   (fingerprint IS NOT NULL AND work_quarter IS NULL AND part_count IS NULL)
                   OR (fingerprint IS NULL AND work_quarter IS NOT NULL
                       AND part_count IS NOT NULL AND part_count BETWEEN 2 AND 1000)
               )"""
    )
    op.execute(
        """CREATE TABLE applied_work_snapshot_parts (
               batch_id UUID NOT NULL REFERENCES applied_work_snapshots(batch_id),
               delivery_id UUID NOT NULL,
               part_number INTEGER NOT NULL CHECK (part_number BETWEEN 1 AND 1000),
               fingerprint TEXT NOT NULL,
               client_codes TEXT[] NOT NULL,
               PRIMARY KEY (batch_id, delivery_id),
               UNIQUE (batch_id, part_number)
           )"""
    )


def downgrade():
    op.execute(
        """DO $$
           BEGIN
               IF EXISTS (SELECT 1 FROM applied_work_snapshot_parts) THEN
                   RAISE EXCEPTION 'Cannot downgrade while multipart delivery receipts exist';
               END IF;
           END $$"""
    )
    op.execute("DROP TABLE applied_work_snapshot_parts")
    op.execute(
        """ALTER TABLE applied_work_snapshots
               DROP CONSTRAINT ck_work_snapshot_delivery_mode,
               DROP COLUMN work_quarter,
               DROP COLUMN part_count,
               ALTER COLUMN fingerprint SET NOT NULL"""
    )
