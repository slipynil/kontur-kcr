-- Identity DB — Gateway / Identity Service (Go)
-- spec.md §2.1; docs/db/README.md

CREATE TABLE users (
    id            UUID        PRIMARY KEY,
    login         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL CHECK (role IN ('ADMIN', 'OPERATOR')),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE users IS 'Учётные записи пользователей (spec.md §2.1). Удаления нет — деактивация is_active.';
COMMENT ON COLUMN users.role IS 'ADMIN | OPERATOR';
