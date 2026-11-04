-- Operator Work DB — Operator Work Service (Python), решение 5
-- spec.md §2.4; docs/db/README.md
-- kcr_code и operator_id — логические ссылки на Clients DB / Identity DB
-- (между БД сервисов физических FK нет, ADR-0001).

-- ---------------------------------------------------------------------------
-- Лента событий клиента (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE client_events (
    id                     UUID        PRIMARY KEY,
    kcr_code               TEXT        NOT NULL,
    author_id              UUID        NOT NULL,
    event_type             TEXT        NOT NULL CHECK (event_type IN (
                                           'CALL_RESULT', 'HISTORICAL_COMMENT', 'EMAIL_SENT',
                                           'STAGE_CHANGE', 'CLAIM_UPDATE', 'OPERATOR_ASSIGN')),
    call_result            TEXT        CHECK (
                                           (event_type = 'CALL_RESULT' AND call_result IN
                                               ('AGREEMENT', 'REFUSAL', 'NO_ANSWER', 'WRONG_NUMBER'))
                                           OR (event_type <> 'CALL_RESULT' AND call_result IS NULL)),
    promised_date          DATE,
    comment_text           TEXT        NOT NULL,
    historical_event_date  TIMESTAMPTZ,            -- дата в прошлом для «прошлого комментария»
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_client ON client_events (kcr_code, created_at DESC);
CREATE INDEX idx_events_promised ON client_events (promised_date) WHERE promised_date IS NOT NULL;

COMMENT ON TABLE client_events IS 'Лента событий клиента (spec.md §2.4). Append-only: не редактируется и не удаляется.';
COMMENT ON COLUMN client_events.promised_date IS 'Обещанный срок; «актуальный» срок для «Сегодня нужно» — из последнего события с непустым promised_date.';
COMMENT ON COLUMN client_events.historical_event_date IS 'Когда событие произошло на самом деле (внесено задним числом); created_at — когда внесено.';

-- ---------------------------------------------------------------------------
-- Закрепление клиентов за операторами
-- ---------------------------------------------------------------------------
CREATE TABLE client_assignments (
    id          UUID        PRIMARY KEY,
    kcr_code    TEXT        NOT NULL,
    operator_id UUID        NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,             -- NULL = активное закрепление
    released_by UUID
);

-- Максимум одно активное закрепление на клиента; take = INSERT в транзакции,
-- конфликт уникальности → 409 с именем владельца (spec.md §1.2, §3.3)
CREATE UNIQUE INDEX uq_assignment_active
    ON client_assignments (kcr_code) WHERE released_at IS NULL;

CREATE INDEX idx_assignments_operator ON client_assignments (operator_id, released_at);

COMMENT ON TABLE client_assignments IS 'Закрепление за оператором + история. Атомарность take — SELECT FOR UPDATE / INSERT в одной ACID-транзакции (spec.md §1.2).';

-- ---------------------------------------------------------------------------
-- История этапов работы
-- ---------------------------------------------------------------------------
CREATE TABLE client_stages (
    id         UUID        PRIMARY KEY,
    kcr_code   TEXT        NOT NULL,
    stage      TEXT        NOT NULL CHECK (stage IN
                            ('POOL', 'IN_WORK', 'WAIT_DEADLINE', 'CLAIM', 'CLOSED')),
    changed_by UUID        NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    comment    TEXT
);

CREATE INDEX idx_stages_client ON client_stages (kcr_code, changed_at DESC);

COMMENT ON TABLE client_stages IS 'История этапов; актуальный этап — последняя запись. Смена этапа дублируется событием STAGE_CHANGE в client_events, взятие в работу — OPERATOR_ASSIGN.';
