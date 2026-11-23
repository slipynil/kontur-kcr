# Errors History Service

Сервис принимает и хранит строки `import_errors` пакетов реестра и квартального среза. Единственный источник — Import Worker. Пользовательское чтение через Gateway предназначено только для `ADMIN`. Поставка самостоятельного сервиса включает внутренний API, миграцию и локальный Docker Compose. Подключение Worker, перенос прежних ошибок и маршрут Gateway идут отдельными этапами. [ADR-0021](../../docs/adr/0021-errors-history-service.md)

## Запуск

Из `services/errors-history` выполните команды.

```sh
docker compose up --build -d --wait
curl http://127.0.0.1:8006/health/ready
```

Compose запускает PostgreSQL 17 с постоянным томом, применяет миграции отдельным контейнером и открывает API на `127.0.0.1:8006`. Контейнер слушает порт 8000. При необходимости скопируйте `.env.example` в `.env`, чтобы переопределить порты или пароль локальной базы.

Для запуска без Docker нужны Python 3.14, Poetry 2.5.1 и подключение к своей PostgreSQL. Установите переменную `ERRORS_HISTORY_DATABASE_URL` с URL драйвера `postgresql+psycopg`.

```sh
poetry install
poetry run alembic upgrade head
poetry run uvicorn errors_history.api:create_app --factory --host 127.0.0.1 --port 8006 --no-access-log
```

## API

- `POST /internal/v1/import-errors/bulk` сохраняет до 1000 ошибок одной транзакцией и возвращает `inserted` и `existing` по уникальным ID. При конфликте `409 IMPORT_ERROR_CONFLICT` ответ указывает `source_error_id`.
- `GET /internal/v1/clients/{kcr_code}/import-errors` читает историю по точному коду с ведущими нулями. Параметры `page`, `page_size` и необязательный `import_type`.
- `GET /internal/v1/imports/{batch_id}/errors` включает ошибки без КЦР. Принимает `page` и `page_size`.
- `GET /health/live` проверяет процесс. `GET /health/ready` проверяет доступность базы и текущую ревизию Alembic.

Оба маршрута чтения возвращают `items`, `page`, `page_size` и `has_more`. Свежие записи идут первыми, затем ошибки без даты. При одинаковых датах порядок определяет убывающий `source_error_id`. [Внутренний контракт и примеры](../../docs/api/internal/errors-history.yaml)

Для ручной проверки на локальной базе отправьте пробную ошибку, затем прочитайте её по коду. Повтор POST возвращает `inserted: 0`, `existing: 1`.

```sh
curl -X POST http://127.0.0.1:8006/internal/v1/import-errors/bulk \
  -H 'Content-Type: application/json' \
  -d '{"errors":[{"source_error_id":701,"batch_id":"019aab49-2300-7000-8000-000000000001","import_type":"REGISTRY","kcr_code":"000123","sheet":"Реестр","row_number":12,"reason":"Дубликат КЦР","recorded_at":"2026-11-23T12:00:00Z"}]}'
curl http://127.0.0.1:8006/internal/v1/clients/000123/import-errors
```

## Проверки

```sh
poetry check --lock
poetry run ruff check .
poetry run ruff format --check .
docker compose -p kontur-errors-history-test -f compose.yaml -f compose.test.yaml \
  run --build --rm errors-history-tests
```

Для `poetry run pytest --cov=errors_history` установите `ERRORS_HISTORY_TEST_ADMIN_URL`. Тесты с PostgreSQL создают и удаляют отдельные временные базы, поэтому тестовой роли требуется право `CREATE DATABASE`. Без переменной эти тесты пропускаются.
