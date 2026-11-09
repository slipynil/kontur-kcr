-- Import DB — Import Worker (Python), решение 6
-- spec.md §2.5, §3.2; docs/db/README.md
-- 1 загруженный файл = 1 пакет.

-- ---------------------------------------------------------------------------
-- Журнал импортов
-- ---------------------------------------------------------------------------
CREATE TABLE import_batches (
    id            UUID        PRIMARY KEY,
    uploaded_by   UUID        NOT NULL,   -- логическая ссылка на Identity DB users.id
    selected_quarter TEXT NOT NULL CHECK (selected_quarter ~ '^\d{4}_Q[1-4]$'),
    import_type TEXT NOT NULL CHECK (import_type IN ('REGISTRY', 'SNAPSHOT')),
    snapshot_date DATE NOT NULL,
    filename      TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'PROCESSING'
                  CHECK (status IN ('PROCESSING', 'COMPLETED', 'ROLLED_BACK', 'FAILED')),
    total_rows    INTEGER     NOT NULL DEFAULT 0,
    success_count INTEGER     NOT NULL DEFAULT 0,
    error_count   INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    file_sha256   TEXT,
    published_at  TIMESTAMPTZ,
    apply_payload JSONB,
    apply_started_at TIMESTAMPTZ,
    clients_applied_at TIMESTAMPTZ
);

CREATE INDEX idx_import_batches_unpublished ON import_batches (created_at)
    WHERE published_at IS NULL AND file_sha256 IS NOT NULL AND status = 'PROCESSING';
CREATE UNIQUE INDEX uq_import_batches_active_apply ON import_batches ((TRUE))
    WHERE apply_started_at IS NOT NULL AND apply_payload IS NOT NULL;

COMMENT ON TABLE import_batches IS 'Журнал импортов (spec.md §2.5). Статус: PROCESSING → COMPLETED | FAILED; ROLLED_BACK после отката.';
COMMENT ON COLUMN import_batches.uploaded_by IS 'Логическая ссылка на users (Identity DB).';
COMMENT ON COLUMN import_batches.error_count IS 'Дубликат counters — детальные строки в import_errors.';

-- ---------------------------------------------------------------------------
-- Строки с ошибками (материализованный error_log из spec.md §2.5)
-- ---------------------------------------------------------------------------
CREATE TABLE import_errors (
    id         BIGSERIAL   PRIMARY KEY,
    batch_id   UUID        NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    sheet      TEXT        NOT NULL,   -- имя листа («1 кв 2026»)
    row_number INTEGER     NOT NULL,
    kcr_code   TEXT,                  -- если код удалось прочитать
    reason     TEXT        NOT NULL   -- «нет Кода КЦР», «дубликат в листе», «код вне реестра», «составной период», «дробное значение»
);

CREATE INDEX idx_import_errors_batch ON import_errors (batch_id);

COMMENT ON TABLE import_errors IS 'Отсеянные строки файла. Основа виджета скачивания .xlsx с ошибками (spec.md §4.1).';

CREATE TABLE import_observations (
    fingerprint TEXT NOT NULL,
    batch_id UUID NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    PRIMARY KEY (fingerprint, batch_id)
);

-- ---------------------------------------------------------------------------
-- Undo-информация пакета: делает возможным rollback при upsert-срезах
-- (spec.md §3.2.3; следствие решения 3 в docs/db/README.md)
-- Предыдущее состояние фиксирует владелец данных атомарно с их изменением.
-- ---------------------------------------------------------------------------
CREATE TABLE import_batch_undo (
    id                        BIGSERIAL PRIMARY KEY,
    batch_id                  UUID      NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    kcr_code                  TEXT      NOT NULL,
    quarter                   TEXT      NOT NULL,
    action                    TEXT      NOT NULL CHECK (action IN ('CREATE', 'UPDATE')),
    prev_unsubmitted_documents INTEGER,             -- для UPDATE: значение до пакета
    prev_snapshot_date        DATE,                 -- для UPDATE
    previous_state JSONB, -- профиль, теги и происхождение до применения пакета
    prev_import_batch_id      UUID                  -- для UPDATE
);

CREATE UNIQUE INDEX uq_import_batch_undo ON import_batch_undo (batch_id, kcr_code, quarter);

COMMENT ON TABLE import_batch_undo IS 'Откат: CREATE → удалить срез и записи журнала пакета в Clients DB; UPDATE → восстановить prev_* и удалить записи журнала пакета (без журнала истории прошлое значение хранится здесь).';

-- Rollback только последнего применённого пакета; старый пакет -> IMPORT_NOT_LATEST.
-- Ручное удаление очищает связанные данные Clients и начатую работу Operator Work через API.
