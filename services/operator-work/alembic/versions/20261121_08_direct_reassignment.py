"""Replace the reassignment inbox with receipts for immediate take operations."""

from alembic import op

revision = "20261121_08"
down_revision = "20261111_07"


def upgrade():
    op.execute(
        """CREATE TABLE client_take_operations (
               id UUID PRIMARY KEY,
               assignment_id UUID NOT NULL REFERENCES client_assignments(id) ON DELETE CASCADE,
               expected_assignment_id UUID
           )"""
    )
    op.execute(
        "CREATE INDEX idx_take_operations_assignment ON client_take_operations (assignment_id)"
    )
    op.execute("DROP TABLE client_reassignment_requests")
    op.execute(
        """COMMENT ON TABLE client_assignments IS
           'Текущее закрепление и история смены ответственного. Действующий срок ожидания сохраняется при перезакреплении.'"""
    )


def downgrade():
    op.execute("DROP TABLE client_take_operations")
    op.execute("""CREATE TABLE client_reassignment_requests (id UUID PRIMARY KEY,
    kcr_code TEXT NOT NULL,
    work_quarter TEXT NOT NULL,
    expected_assignment_id UUID NOT NULL REFERENCES client_assignments(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL,
    requester_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'REJECTED', 'TRANSFERRED', 'CANCELLED')),
    resolved_at TIMESTAMPTZ,
    CHECK (owner_id <> requester_id),
    CHECK (expires_at = created_at + INTERVAL '1 hour'),
    CHECK ((status = 'PENDING') = (resolved_at IS NULL)),
    FOREIGN KEY (kcr_code, work_quarter)
        REFERENCES client_work_cycles(kcr_code, work_quarter) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_pending_reassignment
    ON client_reassignment_requests (kcr_code, work_quarter) WHERE status = 'PENDING';

CREATE INDEX idx_reassignment_due
    ON client_reassignment_requests (expires_at) WHERE status = 'PENDING';

CREATE INDEX idx_reassignment_owner ON client_reassignment_requests (owner_id, created_at DESC);
CREATE INDEX idx_reassignment_requester ON client_reassignment_requests (requester_id, created_at DESC);
""")
