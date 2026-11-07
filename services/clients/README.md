# Clients Service

Первый этап реализации: приложение FastAPI, конфигурация PostgreSQL, health endpoints и Alembic-миграция Clients DB. Бизнес-маршруты внутреннего OpenAPI пока не реализованы. Миграции не запускаются автоматически при старте HTTP-приложения.

## Запуск через Docker Compose

Из каталога `services/clients`:

```powershell
docker compose up --build -d
docker compose logs migrate clients
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Compose поднимает PostgreSQL, применяет миграции отдельным заданием и запускает HTTP-сервис. Порты привязаны к localhost. Демонстрационные пароли предназначены только для локальной разработки. БД сохраняется в volume при `docker compose down`.

## Запуск Python локально

Python 3.13 и PostgreSQL 17. Из каталога `services/clients`:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.lock
.venv/Scripts/python.exe -m pip install --no-deps -e .
$env:CLIENTS_DATABASE_URL = 'postgresql+psycopg://clients:clients_local@localhost:5433/clients'
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/uvicorn.exe clients_service.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Для запуска только БД можно использовать `docker compose up -d db`. `.env.example` содержит пример URL; приложение читает переменные окружения, файл `.env` автоматически не загружает. URL обязателен; секретное значение скрыто в представлении настроек. Валидационные ошибки настроек не следует публиковать в публичном API.

`/health/live` проверяет работу HTTP-процесса. `/health/ready` возвращает 200 при доступной БД с ожидаемой ревизией Alembic, иначе 503 с обезличенным ответом. Таймауты подключения и SQL задаются `CLIENTS_DATABASE_CONNECT_TIMEOUT` (секунды) и `CLIENTS_DATABASE_STATEMENT_TIMEOUT_MS`. Миграции используют тот же URL и таймауты.

## Проверки

```powershell
.venv/Scripts/ruff.exe check .
.venv/Scripts/python.exe -m pytest -m 'not postgres'
# PostgreSQL-проверки создают временную БД и удаляют только её.
# У пользователя тестового подключения должно быть право CREATE DATABASE.
$env:CLIENTS_TEST_ADMIN_URL = 'postgresql+psycopg://clients:clients_local@localhost:5433/postgres'
.venv/Scripts/python.exe -m pytest -m postgres
```

Без тестового URL интеграционные тесты явно пропускаются. Они проверяют upgrade/repeat/downgrade/upgrade, readiness, теги, ограничения и каскад внутри Clients DB; соседние сервисы не затрагиваются.

`requirements.lock` фиксирует runtime-зависимости, `requirements-dev.lock` — окружение разработки. Начальная миграция содержит замороженную копию утверждённого DDL; последующие изменения схемы оформляются новыми миграциями после обновления спецификации. Начальную миграцию после применения на общем стенде не менять.

Следующий этап — контракт импорта и запись данных. Межсервисные заголовки, авторизация и распределённые apply/rollback/delete пока требуют согласования. HTTP-сервис сейчас предоставляет только технические проверки и документацию FastAPI.
