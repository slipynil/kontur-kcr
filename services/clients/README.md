# Clients Service

Сервис ведёт реестр клиентов, квартальные срезы и историю. Миграции применяются отдельной командой перед запуском приложения.

## Запуск через Docker Compose

Из каталога `services/clients`:

```powershell
docker compose up --build -d
docker compose logs migrate clients
Invoke-RestMethod http://localhost:8001/health/live
Invoke-RestMethod http://localhost:8001/health/ready
```

Compose поднимает PostgreSQL, применяет миграции отдельным заданием и запускает HTTP-сервис. Порты привязаны к localhost. Демонстрационные пароли предназначены только для локальной разработки. БД сохраняется в volume при `docker compose down`.

## Запуск Python локально

Python 3.14, Poetry 2.5.1 и PostgreSQL 17. Команды запуска из каталога `services/clients`.

```powershell
poetry env use 3.14
poetry install --no-interaction
$env:CLIENTS_DATABASE_URL = 'postgresql+psycopg://clients:clients_local@localhost:5433/clients'
poetry run alembic upgrade head
poetry run uvicorn clients_service.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Для запуска только БД можно использовать `docker compose up -d db`. `.env.example` содержит пример URL; приложение читает переменные окружения, файл `.env` автоматически не загружает. URL обязателен; секретное значение скрыто в представлении настроек. Валидационные ошибки настроек не следует публиковать в публичном API.

`/health/live` проверяет работу HTTP-процесса. `/health/ready` возвращает 200 при доступной БД с ожидаемой ревизией Alembic, иначе 503 с обезличенным ответом. Таймауты подключения и SQL задаются `CLIENTS_DATABASE_CONNECT_TIMEOUT` (секунды) и `CLIENTS_DATABASE_STATEMENT_TIMEOUT_MS`. Миграции используют тот же URL и таймауты.

## Проверки

```powershell
poetry check --lock
poetry run ruff check .
poetry run ruff format --check .
# PostgreSQL-проверки создают временную БД и удаляют только её.
# У пользователя тестового подключения должно быть право CREATE DATABASE.
$env:CLIENTS_TEST_ADMIN_URL = 'postgresql+psycopg://clients:clients_local@localhost:5433/postgres'
poetry run pytest --cov=. --cov-report=term --cov-report=json
```

Без тестового URL интеграционные тесты явно пропускаются. Они проверяют upgrade/repeat/downgrade/upgrade, readiness, теги, ограничения и каскад внутри Clients DB; соседние сервисы не затрагиваются.

`poetry.lock` фиксирует зависимости приложения и разработки. `poetry build` собирает Python-пакет. Docker использует тот же lock-файл и устанавливает только зависимости приложения. Начальная миграция содержит копию утверждённого DDL. Последующие изменения схемы оформляются новыми миграциями после обновления спецификации.

## Применение пакета в изолированной разработке

Пример JSON конверта REGISTRY:

```json
{
  "batch_id": "00000000-0000-0000-0000-000000000101",
  "uploaded_by": "00000000-0000-0000-0000-000000000001",
  "import_type": "REGISTRY",
  "items": [{"client": {"kcr_code": "example-001", "organization_name": "Тестовый клиент"}}]
}
```

Для SNAPSHOT используйте новый `batch_id`, укажите `import_type=SNAPSHOT`, `selected_quarter` и `snapshot_date`. Добавьте в строку `"snapshot": {"quarter": "2026_Q1", "total_documents": 100, "unsubmitted_documents": 7}`. Правила применения пакетов приведены в [ADR-0004](../../docs/adr/0004-clients-import-application.md) и [ADR-0016](../../docs/adr/0016-snapshot-only-import-metadata.md).

## Чтение в изолированной разработке

```powershell
Invoke-RestMethod 'http://localhost:8000/internal/v1/clients?search=123&tags=VIP,INTEGRATOR&page=1&page_size=20'
Invoke-RestMethod 'http://localhost:8000/internal/v1/clients?quarter=2026_Q3&dynamics=REDUCING'
Invoke-RestMethod 'http://localhost:8000/internal/v1/clients/example-001?quarter=2026_Q3&snapshots_limit=8'
Invoke-RestMethod 'http://localhost:8000/internal/v1/clients/example-001/snapshots/history?quarter=2026_Q2&limit=20&offset=0'
```

Параметры и поля ответа описаны в [OpenAPI](../../docs/api/internal/clients-service.yaml), правила расчёта — в [ADR-0005](../../docs/adr/0005-clients-read-api-and-quarter-dynamics.md).

## Откат и удаление в изолированной разработке

```powershell
$body = @{ operation_id = [guid]::NewGuid().ToString(); requested_by = '00000000-0000-0000-0000-000000000001' } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body $body 'http://localhost:8000/internal/v1/import/00000000-0000-0000-0000-000000000101/rollback'
# Для каждого нового действия создавайте новый operation_id; при повторе сохраняйте прежний body.
$body = @{ operation_id = [guid]::NewGuid().ToString(); requested_by = '00000000-0000-0000-0000-000000000001' } | ConvertTo-Json
Invoke-RestMethod -Method Delete -ContentType 'application/json' -Body $body 'http://localhost:8000/internal/v1/clients/example-001/snapshots/2026_Q1'
```

Для полного удаления передайте такое же тело в `DELETE /internal/v1/clients/{kcr_code}`. [Порядок действий координатора](../../docs/api/import-operator-work-contract.md#откат-и-выборочная-очистка).

## Числовые фильтры выбранного квартала

```powershell
Invoke-RestMethod 'http://localhost:8000/internal/v1/clients?quarter=2026_Q3&min_unsubmitted_documents=10&max_unsubmitted_documents=100&min_unsubmitted_percent=5&max_unsubmitted_percent=50'
```

Правила фильтрации и поля сводки за квартал описаны в [ADR-0007](../../docs/adr/0007-clients-numeric-filters-and-verification.md).

## Подключение соседних сервисов

Маршруты Clients доступны при запуске. Серверный Compose оставляет HTTP-порт Clients во внутренней сети. Доступ пользователей проверяет Gateway. [Серверный Compose](../../server/compose.yaml), [ADR-0010](../../docs/adr/0010-no-interservice-token-auth.md).

Clients проверяет срок хранения копий при запуске и затем каждый час. Время первого подтверждения записывает маршрут `PUT /internal/v1/import/{batch_id}/rollback-confirmation`. Import Worker вызывает его после завершения отката во всех затронутых сервисах. Через 90 дней Clients удаляет копии подтверждённо отменённого пакета и сохраняет результаты команд для повторов.

## Проверка отдельного HTTP-процесса

```powershell
python scripts/check_http.py --base-url http://127.0.0.1:8001
```

Скрипт создаёт синтетические записи. Запускайте его на loopback с отдельной тестовой БД. Для Python-запуска из примера выше подставьте порт `8000`.
