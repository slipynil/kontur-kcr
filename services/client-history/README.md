# Client History Service

Сервис хранит хронику взаимодействия клиента: звонки, прошлые комментарии и комментарии текущего цикла, смены ответственного, передачу в обзвон и смены этапов ([ADR-0020](../../docs/adr/0020-client-history-service.md)). Рабочих правил сервис не содержит: циклы, закрепления, этапы и сроки остаются в Operator Work, который передаёт факты и команды очистки через свою таблицу `history_outbox`.

## Локальный запуск

Из каталога `services/client-history`:

```sh
cp .env.example .env
docker compose up --build -d
curl http://127.0.0.1:8005/health/ready
```

Compose поднимает PostgreSQL, применяет миграции отдельным заданием и запускает HTTP-сервис. Хостовый порт — 8005 ([таблица портов](../../docs/local-development.md)).

Без Docker нужны Python 3.14 и Poetry 2.5.1:

```sh
poetry env use 3.14
poetry install
export CLIENT_HISTORY_DATABASE_URL='postgresql+psycopg://client_history:client-history-dev@127.0.0.1:5432/client_history'
poetry run alembic upgrade head
poetry run uvicorn client_history.api:create_app --factory --host 127.0.0.1 --port 8005 --no-access-log
```

## Настройки

| Переменная | Назначение |
|---|---|
| `CLIENT_HISTORY_DATABASE_URL` | Обязательна. URL Client History DB (драйвер psycopg). |
| `CLIENT_HISTORY_GATEWAY_URL` | Базовый URL Gateway для имён авторов (`GET /internal/v1/users?ids=`). Без неё пакет с неназванным автором не принимается: `503`. |
| `CLIENT_HISTORY_CLIENTS_URL` | Базовый URL Clients Service. Без неё код без записей всегда читается как пустая страница. |
| `CLIENT_HISTORY_UPSTREAM_TIMEOUT` | Таймаут обращений к соседним сервисам в секундах, по умолчанию 3. |
| `CLIENT_HISTORY_PORT` | Порт процесса, по умолчанию 8000. |

Адреса должны быть доступны из контейнера: `127.0.0.1` внутри контейнера указывает на сам контейнер. В стеке dev-сервера это `http://gateway:8080` и `http://clients:8000`. Проверку личности, роли и видимости клиента для оператора выполняет Gateway ([ADR-0010](../../docs/adr/0010-no-interservice-token-auth.md), [ADR-0014](../../docs/adr/0014-operator-client-visibility.md)).

## API

Контракт — [docs/api/internal/client-history.yaml](../../docs/api/internal/client-history.yaml).

- `POST /internal/v1/history/events` — приём пакета фактов, до 1000 записей. Пакет сохраняется целиком или не сохраняется. Повтор с тем же `id` и содержимым не создаёт копию; с иным содержимым — `409 HISTORY_EVENT_CONFLICT`. Если источник не передал `actor_name`, сервис читает логин в Gateway и запоминает его; недоступный Gateway — `503`, ничего не сохранено.
- `POST /internal/v1/history/cleanup` — очистка. `CLIENT` удаляет все записи клиента, `TARGETED` — записи из `event_ids` и записи циклов из `cycle_ids`. Повтор с тем же `operation_id` возвращает первый результат и не затрагивает более поздние записи; иной состав с тем же ID — `409 OPERATION_ID_CONFLICT`.
- `GET /internal/v1/clients/{kcr_code}/events` — страница хроники от новых к старым по `occurred_at`, затем по `seq`. Параметры: `work_quarter`, `page`, `page_size`. Если у кода нет записей, сервис уточняет наличие клиента в Clients Service: отсутствующий код — `404 CLIENT_NOT_FOUND`, клиент реестра без истории — пустая страница.
- `/health/live` и `/health/ready` — готовность требует доступной БД с ожидаемой ревизией Alembic.

Ответ чтения содержит `seq`, `source` и устаревшие поля публичного контракта (`author_id`, `comment_text`, `created_at`, `historical_event_date`, `call_result`, `promised_date`, `wait_until`, `stage`). Они выводятся из записи, чтобы Gateway отдавал ленту без преобразований.

## Перенос прежних событий

Записи `client_events`, созданные до ADR-0020, переносятся разовым скриптом. Он читает Operator Work DB только на чтение и отправляет записи обычным приёмом, поэтому повтор безопасен.

```sh
poetry run python -m client_history.backfill \
  --operator-work-database-url 'postgresql+psycopg://operator_work:operator_work@127.0.0.1:5432/operator_work' \
  --client-history-url http://127.0.0.1:8005
```

Имена авторов заполняет сервис, поэтому на время переноса должен быть задан `CLIENT_HISTORY_GATEWAY_URL`. Прежние записи не хранят назначение и прежнего ответственного — эти поля остаются пустыми; повод смены этапа берётся из формулировки старой записи.

## Проверки

```sh
poetry check --lock
poetry run ruff check .
poetry run ruff format --check .
CLIENT_HISTORY_TEST_ADMIN_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres' \
  poetry run pytest --cov=client_history
docker compose -p kontur-client-history-test -f compose.yaml -f compose.test.yaml run --build --rm client-history-tests
```

Тесты создают и удаляют собственные временные базы; пользователю тестового подключения нужно право `CREATE DATABASE`. Без `CLIENT_HISTORY_TEST_ADMIN_URL` проверки с PostgreSQL пропускаются. Отдельный тест сравнивает схему миграции с утверждённым DDL [docs/db/ddl/client_history.sql](../../docs/db/ddl/client_history.sql).

## Состояние подключения

Сервис принимает и отдаёт хронику, но источники и Gateway подключаются отдельными задачами: `history_outbox` и отправка фактов в Operator Work, маршрут чтения ленты в Gateway и хроника в карточке клиента (`spec.md` §5, PR #15 и #16). Публикация образа и добавление сервиса в стек dev-сервера требуют отдельного решения о CI.
