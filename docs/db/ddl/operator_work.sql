-- Operator Work DB — Operator Work Service (Python), решение 5
-- spec.md §2.4; docs/db/README.md
-- kcr_code и operator_id — логические ссылки на Clients DB / Identity DB
-- (между БД сервисов физических FK нет, ADR-0001).

-- Квартальный пул; квартал работы отделён от отчётных кварталов документов.
CREATE TABLE client_work_cycles (
    kcr_code TEXT NOT NULL,
    work_quarter TEXT NOT NULL CHECK (work_quarter ~ '^\d{4}_Q[1-4]$'),
    wait_until DATE, -- конкретная дата оператора; защита действует пока business_date < wait_until
    wait_source_work_quarter TEXT CHECK (wait_source_work_quarter IS NULL OR wait_source_work_quarter ~ '^\d{4}_Q[1-4]$'),
    source_import_batch_id UUID, -- логическая ссылка на Import DB
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kcr_code, work_quarter)
);
COMMENT ON TABLE client_work_cycles IS 'Участие клиента в квартальном обзвоне. Квартал выбирается при загрузке. При действующем сроке новый цикл сохраняет WAIT_DEADLINE, оператора и дату. После срока при остатке доступен POOL.';

-- ---------------------------------------------------------------------------
-- Лента событий клиента (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE client_events (
    id                     UUID        PRIMARY KEY,
    kcr_code               TEXT        NOT NULL,
    work_quarter           TEXT, -- NULL для старого комментария без установленного цикла
    author_id              UUID        NOT NULL,
    event_type             TEXT        NOT NULL CHECK (event_type IN (
                                           'CALL_RESULT', 'HISTORICAL_COMMENT', 'EMAIL_SENT',
                                           'STAGE_CHANGE', 'CLAIM_UPDATE', 'OPERATOR_ASSIGN')),
    call_result            TEXT        CHECK (
                                           (event_type = 'CALL_RESULT' AND call_result IN
                                               ('AGREEMENT', 'REFUSAL', 'NO_ANSWER', 'WRONG_NUMBER'))
                                           OR (event_type <> 'CALL_RESULT' AND call_result IS NULL)),
    stage TEXT CHECK (stage IS NULL OR stage IN ('POOL', 'IN_WORK', 'WAIT_DEADLINE', 'CLAIM', 'CLOSED')),
    wait_until DATE, -- срок на момент записи события
    promised_date          DATE,
    comment_text           TEXT        NOT NULL,
    historical_event_date  TIMESTAMPTZ,            -- дата в прошлом для «прошлого комментария»
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (event_type = 'HISTORICAL_COMMENT' OR work_quarter IS NOT NULL),
    CHECK (event_type <> 'STAGE_CHANGE' OR stage IS NOT NULL),
    CHECK (stage IS DISTINCT FROM 'WAIT_DEADLINE' OR wait_until IS NOT NULL),
    FOREIGN KEY (kcr_code, work_quarter) REFERENCES client_work_cycles (kcr_code, work_quarter) ON DELETE CASCADE
);

CREATE INDEX idx_events_client ON client_events (kcr_code, created_at DESC);
CREATE INDEX idx_events_cycle ON client_events (kcr_code, work_quarter, created_at DESC);
CREATE INDEX idx_events_promised ON client_events (promised_date) WHERE promised_date IS NOT NULL;

COMMENT ON TABLE client_events IS 'Лента событий клиента (spec.md §2.4). Обычная лента append-only; разрешённое каскадное удаление и последний rollback очищают связанные записи.';
COMMENT ON COLUMN client_events.promised_date IS 'Обещанный срок передачи документов; хранится в истории и отображается в карточке. Время звонка оператор выбирает самостоятельно.';
COMMENT ON COLUMN client_events.historical_event_date IS 'Когда событие произошло на самом деле (внесено задним числом); created_at — когда внесено.';

-- ---------------------------------------------------------------------------
-- Закрепление клиентов за операторами
-- ---------------------------------------------------------------------------
CREATE TABLE client_assignments (
    id          UUID        PRIMARY KEY,
    kcr_code    TEXT        NOT NULL,
    work_quarter TEXT       NOT NULL,
    operator_id UUID        NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,             -- NULL = активное закрепление
    released_by UUID,
    FOREIGN KEY (kcr_code, work_quarter) REFERENCES client_work_cycles (kcr_code, work_quarter) ON DELETE CASCADE
);

-- Максимум одно активное закрепление на клиента в квартальном цикле; take сериализуется по строке цикла;
-- при отсутствии действующего ожидания: завершить прежнюю запись и создать новую
-- в одной транзакции с проверкой wait_until и записью OPERATOR_ASSIGN (spec.md §3.3).
-- WAIT_DEADLINE блокирует перезакрепление до даты оператора, включая новый квартал.
-- После даты при остатке клиент возвращается в POOL; переход идемпотентен.
CREATE UNIQUE INDEX uq_assignment_active
    ON client_assignments (kcr_code, work_quarter) WHERE released_at IS NULL;

CREATE INDEX idx_assignments_operator ON client_assignments (operator_id, work_quarter, released_at);

COMMENT ON TABLE client_assignments IS 'Закрепление за оператором + история. Взятие и перезакрепление атомарны в рамках work_quarter; WAIT_DEADLINE до wait_until блокирует смену ответственного через границу квартала. Старые циклы сохраняются как история. released_at/released_by завершают прежнее закрепление; отдельных действий освобождения и снятия нет.';

-- ---------------------------------------------------------------------------
-- История этапов работы
-- ---------------------------------------------------------------------------
CREATE TABLE client_stages (
    id         UUID        PRIMARY KEY,
    kcr_code   TEXT        NOT NULL,
    work_quarter TEXT      NOT NULL,
    stage      TEXT        NOT NULL CHECK (stage IN
                            ('POOL', 'IN_WORK', 'WAIT_DEADLINE', 'CLAIM', 'CLOSED')),
    changed_by UUID        NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    comment    TEXT,
    FOREIGN KEY (kcr_code, work_quarter) REFERENCES client_work_cycles (kcr_code, work_quarter) ON DELETE CASCADE
);

CREATE INDEX idx_stages_client ON client_stages (kcr_code, work_quarter, changed_at DESC);

COMMENT ON TABLE client_stages IS 'История этапов по квартальным циклам; актуальный этап — последняя запись выбранного цикла. WAIT_DEADLINE действует до wait_until, а не до конца квартала. Смена этапа дублируется событием STAGE_CHANGE в client_events, взятие в работу — OPERATOR_ASSIGN.';
