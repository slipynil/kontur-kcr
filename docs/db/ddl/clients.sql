-- Clients DB — Clients Service (Python)
-- spec.md §2.2, §2.3; docs/db/README.md
-- Закрепление и этап живут в Operator Work DB (решение 5).

-- ---------------------------------------------------------------------------
-- Реестр клиентов (лист «Все КЦР»)
-- ---------------------------------------------------------------------------
CREATE TABLE clients (
    kcr_code         TEXT        PRIMARY KEY,          -- Код КЦР, первичный бизнес-ключ
    inn              TEXT,                              -- один ИНН может иметь несколько кодов КЦР
    kpp              TEXT,
    organization_name TEXT,
    status           TEXT        NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE', 'INACTIVE')),
    is_blocked       BOOLEAN     NOT NULL DEFAULT FALSE,
    manager_name     TEXT,
    contacts         JSONB,                             -- контакты для связи (spec.md §2.2)
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    inherited_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_work_status  TEXT,                              -- исходный статус выгрузки («VIP+Итегратор», «Не нужно работать»)
    source           TEXT        NOT NULL DEFAULT 'REGISTRY'
                      CHECK (source IN ('REGISTRY', 'SNAPSHOT')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_clients_inn ON clients (inn);

COMMENT ON TABLE clients IS 'Реестр клиентов КЦР (spec.md §2.2). Наполняется файлом реестра.';
COMMENT ON COLUMN clients.raw_work_status IS 'Исходный рабочий статус из выгрузки; парсится в client_tags, сам по себе не показывается в UI (может содержать запрещённые термины).';
COMMENT ON COLUMN clients.source IS 'REGISTRY — из файла реестра; SNAPSHOT — зарезервировано, если позже решим создавать клиентов из срезов.';

-- ---------------------------------------------------------------------------
-- Признаки клиента: справочник тегов + связь M:N (решение 1)
-- ---------------------------------------------------------------------------
CREATE TABLE tags (
    id    SMALLINT   PRIMARY KEY,
    code  TEXT       NOT NULL UNIQUE,   -- VIP, NOVICE, INTEGRATOR, TECH_PARTNER, MPP, DO_NOT_WORK
    name  TEXT       NOT NULL
);

CREATE TABLE client_tags (
    kcr_code TEXT       NOT NULL REFERENCES clients (kcr_code) ON DELETE CASCADE,
    tag_id   SMALLINT   NOT NULL REFERENCES tags (id)          ON DELETE RESTRICT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kcr_code, tag_id)
);

COMMENT ON TABLE tags IS 'Справочник признаков клиента. Стартовые значения из выгрузки (docs/db/README.md).';

-- Стартовый справочник (идентификаторы фиксированы — используются в фильтрах API)
INSERT INTO tags (id, code, name) VALUES
    (1, 'VIP',          'VIP'),
    (2, 'NOVICE',       'Новичок'),
    (3, 'INTEGRATOR',   'Интегратор'),
    (4, 'TECH_PARTNER', 'Техпартнер'),
    (5, 'MPP',          'МПП'),
    (6, 'DO_NOT_WORK',  'Не нужно работать');

-- ---------------------------------------------------------------------------
-- Срезы по кварталам: одна запись на (kcr_code, quarter), upsert (решение 3)
-- ---------------------------------------------------------------------------
CREATE TABLE quarter_snapshots (
    kcr_code              TEXT   NOT NULL REFERENCES clients (kcr_code) ON DELETE CASCADE,
    quarter               TEXT   NOT NULL CHECK (quarter ~ '^\d{4}_Q[1-4]$'),  -- 2026_Q1
    last_observation_quarter TEXT NOT NULL CHECK (last_observation_quarter ~ '^\d{4}_Q[1-4]$'),
    total_documents       INTEGER NOT NULL CHECK (total_documents >= 0),
    unsubmitted_documents INTEGER NOT NULL CHECK (unsubmitted_documents >= 0),
    import_batch_id       UUID   NOT NULL,   -- логическая ссылка на Import DB import_batches.id
    snapshot_date         DATE   NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kcr_code, quarter)
);

CREATE INDEX idx_snapshots_quarter ON quarter_snapshots (quarter);
CREATE INDEX idx_snapshots_batch   ON quarter_snapshots (import_batch_id);  -- для отката

COMMENT ON TABLE quarter_snapshots IS 'Актуальные срезы документов по кварталам (spec.md §2.3). Одна запись на (kcr_code, quarter); история изменений — в quarter_snapshot_history.';
COMMENT ON COLUMN quarter_snapshots.total_documents IS 'Фиксируется при ПЕРВОЙ загрузке квартала, далее не меняется (spec.md §1.2).';
COMMENT ON COLUMN quarter_snapshots.unsubmitted_documents IS 'Непереданные документы; обновляется каждой загрузкой того же квартала.';
COMMENT ON COLUMN quarter_snapshots.import_batch_id IS 'Логическая ссылка на Import DB (между БД физического FK нет): последний пакет, менявший запись.';

-- ---------------------------------------------------------------------------
-- Журнал истории изменения статуса передачи документов (append-only,
-- spec.md §2.4 — обязательное требование; основа расчёта динамики)
-- ---------------------------------------------------------------------------
CREATE TABLE quarter_snapshot_history (
    id                     UUID        PRIMARY KEY,
    kcr_code               TEXT        NOT NULL REFERENCES clients (kcr_code) ON DELETE CASCADE,
    quarter                TEXT        NOT NULL CHECK (quarter ~ '^\d{4}_Q[1-4]$'),
    observation_quarter TEXT NOT NULL CHECK (observation_quarter ~ '^\d{4}_Q[1-4]$'),
    unsubmitted_documents  INTEGER     NOT NULL CHECK (unsubmitted_documents >= 0),
    import_batch_id        UUID        NOT NULL,   -- логическая ссылка на Import DB import_batches.id
    snapshot_date          DATE        NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_snapshot_history_client
    ON quarter_snapshot_history (kcr_code, created_at DESC);
CREATE INDEX idx_snapshot_history_batch ON quarter_snapshot_history (import_batch_id);  -- для отката


COMMENT ON TABLE quarter_snapshot_history IS 'Новые наблюдения, включая неизменный остаток. Исключения append-only: последний rollback и ручное каскадное удаление.';
CREATE TABLE snapshot_field_provenance (
    kcr_code TEXT NOT NULL,
    quarter TEXT NOT NULL,
    field_name TEXT NOT NULL,
    source_batch_id UUID NOT NULL,
    source_quarter TEXT NOT NULL CHECK (source_quarter ~ '^\d{4}_Q[1-4]$'),
    source_snapshot_date DATE NOT NULL,
    PRIMARY KEY (kcr_code, quarter, field_name),
    FOREIGN KEY (kcr_code, quarter) REFERENCES quarter_snapshots (kcr_code, quarter) ON DELETE CASCADE
);
COMMENT ON TABLE snapshot_field_provenance IS 'Происхождение унаследованных полей среза; клиент получает needs_review. Перенос остатка не создаёт второй комплект документов.';

-- ADR-0004: локальное применение пакетов (миграция 0002).
ALTER TABLE clients ADD COLUMN field_sources JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE quarter_snapshot_history
    ADD COLUMN observation_order BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    ADD COLUMN inherited_fields JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX idx_history_observation ON quarter_snapshot_history
    (kcr_code, quarter, snapshot_date DESC, observation_order DESC);

CREATE TABLE clients_import_operations (
    batch_id UUID PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE clients_import_undo (
    batch_id UUID NOT NULL REFERENCES clients_import_operations(batch_id) ON DELETE CASCADE,
    kcr_code TEXT NOT NULL,
    previous_state JSONB NOT NULL,
    created_history_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (batch_id, kcr_code)
);

-- ADR-0006: локальный порядок новых пакетов и результат административных команд.
CREATE SEQUENCE clients_change_order_seq;
ALTER TABLE clients_import_operations
    ADD COLUMN apply_order BIGINT UNIQUE,
    ADD COLUMN rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN rollback_result JSONB;
ALTER TABLE clients_import_operations ALTER COLUMN apply_order
    SET DEFAULT nextval('clients_change_order_seq');
CREATE TABLE clients_data_operations (
    operation_id UUID PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('ROLLBACK', 'DELETE_CLIENT', 'DELETE_QUARTER')),
    kcr_code TEXT,
    quarter TEXT,
    batch_id UUID,
    requested_by UUID NOT NULL,
    change_order BIGINT NOT NULL UNIQUE DEFAULT nextval('clients_change_order_seq'),
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE clients_deleted_history (
    operation_id UUID NOT NULL REFERENCES clients_data_operations(operation_id) ON DELETE CASCADE,
    history_id UUID NOT NULL,
    kcr_code TEXT NOT NULL,
    quarter TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (operation_id, history_id)
);
CREATE INDEX idx_deleted_history_client ON clients_deleted_history(kcr_code);
