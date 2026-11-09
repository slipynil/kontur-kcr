from alembic import op

revision = "0001_import"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE import_batches (
            id UUID PRIMARY KEY,
            uploaded_by UUID NOT NULL,
            selected_quarter TEXT NOT NULL CHECK (selected_quarter ~ '^\d{4}_Q[1-4]$'),
            import_type TEXT NOT NULL CHECK (import_type IN ('REGISTRY', 'SNAPSHOT')),
            snapshot_date DATE NOT NULL,
            filename TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PROCESSING'
                CHECK (status IN ('PROCESSING', 'COMPLETED', 'ROLLED_BACK', 'FAILED')),
            total_rows INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            file_sha256 TEXT,
            published_at TIMESTAMPTZ,
            apply_payload JSONB,
            apply_started_at TIMESTAMPTZ,
            clients_applied_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """CREATE INDEX idx_import_batches_unpublished ON import_batches (created_at)
           WHERE published_at IS NULL AND file_sha256 IS NOT NULL AND status = 'PROCESSING'"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_import_batches_active_apply ON import_batches ((TRUE))
           WHERE apply_started_at IS NOT NULL AND apply_payload IS NOT NULL"""
    )
    op.execute(
        """
        CREATE TABLE import_errors (
            id BIGSERIAL PRIMARY KEY,
            batch_id UUID NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
            sheet TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            kcr_code TEXT,
            reason TEXT NOT NULL
        )
        """
    )
    op.create_index("idx_import_errors_batch", "import_errors", ["batch_id"])
    op.execute(
        """
        CREATE TABLE import_observations (
            fingerprint TEXT NOT NULL,
            batch_id UUID NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
            PRIMARY KEY (fingerprint, batch_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE import_batch_undo (
            id BIGSERIAL PRIMARY KEY,
            batch_id UUID NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
            kcr_code TEXT NOT NULL,
            quarter TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE')),
            prev_unsubmitted_documents INTEGER,
            prev_snapshot_date DATE,
            previous_state JSONB,
            prev_import_batch_id UUID
        )
        """
    )
    op.create_index(
        "uq_import_batch_undo",
        "import_batch_undo",
        ["batch_id", "kcr_code", "quarter"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("import_batch_undo")
    op.drop_table("import_observations")
    op.drop_table("import_errors")
    op.drop_table("import_batches")
