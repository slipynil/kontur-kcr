"""Initial Client History DB, frozen from docs/db/ddl/client_history.sql (ADR-0020)."""

from alembic import op

revision = "0001_client_history"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- Client History DB — Client History Service (Python), ADR-0020
-- spec.md §2.10; docs/db/README.md
-- Хроника взаимодействия клиента: производная запись фактов, только добавление.
-- kcr_code, actor_id и cycle_id — логические ссылки на Clients DB, Identity DB и Operator Work DB
-- (между БД сервисов физических FK нет, ADR-0001).

-- ---------------------------------------------------------------------------
-- Хроника взаимодействия клиента
-- ---------------------------------------------------------------------------
CREATE TABLE history_events (
    id                UUID        PRIMARY KEY,           -- ID записи-источника; для звонков и комментариев — request_id
    kcr_code          TEXT        NOT NULL,              -- логическая ссылка на Clients DB clients.kcr_code
    event_type        TEXT        NOT NULL CHECK (event_type IN (
                                      'CALL_RESULT', 'HISTORICAL_COMMENT', 'COMMENT', 'OPERATOR_ASSIGN',
                                      'PUBLICATION', 'STAGE_CHANGE', 'EMAIL_SENT', 'CLAIM_UPDATE')),
    source            TEXT        NOT NULL CHECK (source IN ('OPERATOR_WORK')), -- сервис, передавший факт
    actor_id          UUID,                              -- логическая ссылка на Identity DB users.id; NULL без автора
    actor_name        TEXT,                              -- снимок логина на момент приёма записи
    work_quarter      TEXT        CHECK (work_quarter IS NULL OR work_quarter ~ '^\d{4}_Q[1-4]$'),
    cycle_id          UUID,                              -- логическая ссылка на Operator Work DB client_work_cycles.cycle_id
    document_quarters TEXT[]      NOT NULL DEFAULT '{}', -- исходные кварталы документов (ADR-0008)
    occurred_at       TIMESTAMPTZ NOT NULL,              -- когда произошло; для прошлого комментария — указанная дата
    recorded_at       TIMESTAMPTZ NOT NULL,              -- когда записано у источника
    seq               BIGINT      GENERATED ALWAYS AS IDENTITY, -- порядок внутри одной секунды
    payload           JSONB       NOT NULL DEFAULT '{}'::jsonb, -- детали типа события; состав проверяет сервис
    fingerprint       TEXT        NOT NULL,              -- хэш содержимого: повтор с иным содержимым — конфликт
    CONSTRAINT ck_history_actor_name CHECK (actor_id IS NOT NULL OR actor_name IS NULL)
);

-- Лента карточки: от новых к старым по дате события.
CREATE INDEX idx_history_events_client ON history_events (kcr_code, occurred_at DESC, seq DESC);
-- Выборочная очистка по циклам обзвона.
CREATE INDEX idx_history_events_cycle ON history_events (kcr_code, cycle_id) WHERE cycle_id IS NOT NULL;

COMMENT ON TABLE history_events IS 'Хроника взаимодействия клиента (spec.md §2.10, ADR-0020). Только добавление; удаляется командой Operator Work при полном удалении клиента, удалении исходного квартала и откате пакета.';
COMMENT ON COLUMN history_events.id IS 'Совпадает с ID записи-источника. Повтор приёма с тем же id и содержимым не создаёт копию, с иным содержимым отвергается.';
COMMENT ON COLUMN history_events.occurred_at IS 'Когда событие произошло; для прошлого комментария — указанная оператором дата. Лента сортируется по этой дате, а не по времени записи.';
COMMENT ON COLUMN history_events.actor_name IS 'Снимок логина на момент приёма записи; автор остаётся виден после удаления пользователя.';

-- ---------------------------------------------------------------------------
-- Журнал очистки: повтор команды возвращает прежний результат
-- ---------------------------------------------------------------------------
CREATE TABLE history_cleanup_operations (
    operation_id UUID PRIMARY KEY,                       -- ID операции координатора (Operator Work client_cleanup_operations)
    kcr_code     TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,                          -- хэш команды: тот же operation_id с иным содержимым — конфликт
    deleted      INTEGER NOT NULL,                       -- число записей, удалённых первым применением
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE history_cleanup_operations IS 'Выполненные команды очистки (ADR-0020). Запись фиксируется той же транзакцией, что и удаление, и хранит только метаданные.';
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.drop_table("history_cleanup_operations")
    op.drop_table("history_events")
