# Clients Service

Реализованы каркас FastAPI, конфигурация PostgreSQL, health endpoints, миграции Clients DB и локальное применение нормализованных пакетов Import по ADR-0004. Чтение реестра, глобальная координация, rollback и delete ещё не реализованы. Миграции не запускаются автоматически при старте HTTP-приложения.

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

## Применение пакета в изолированной разработке

По умолчанию `/internal/v1/import/apply` отсутствует и в маршрутах, и в OpenAPI. Для локальной проверки задайте `CLIENTS_ENABLE_IMPORT_APPLY=true` перед запуском Python-приложения. Настройка не заменяет авторизацию; в Compose она намеренно не включена. Межсервисную авторизацию нужно согласовать до открытия маршрута на общем стенде.

Пакеты REGISTRY создают/обновляют профили и теги. SNAPSHOT принимает только известные КЦР и сохраняет исходный квартал, остаток и историю. Пустой остаток наследуется из последнего наблюдения того же исходного квартала не позже даты нового среза; явный ноль снимает предупреждение. Более ранний срез дополняет историю, а исправление одной даты становится актуальным. Профиль обновляется по переданным полям; отсутствующие ключи частичного профиля не меняют данные. Явная пустота наследует известное поле и отмечает его для проверки.

Пример JSON конверта REGISTRY:

```json
{
  "batch_id": "00000000-0000-0000-0000-000000000101",
  "uploaded_by": "00000000-0000-0000-0000-000000000001",
  "selected_quarter": "2026_Q2",
  "import_type": "REGISTRY",
  "snapshot_date": "2026-04-20",
  "items": [{"client": {"kcr_code": "example-001", "organization_name": "Тестовый клиент"}}]
}
```

Для SNAPSHOT поменяйте batch_id, укажите import_type=SNAPSHOT и добавьте в строку `"snapshot": {"quarter": "2026_Q1", "total_documents": 100, "unsubmitted_documents": 7}`. Числа должны быть нормализованными целыми, без строк/boolean/дробей. Excel нормализует Import, а не этот сервис.

Ответ содержит счётчики, accepted_rows, rejected_rows и errors (индекс строки с единицы, код, поле). Непринятая строка не оставляет частичных изменений профиля. Повтор идентичного batch_id возвращает сохранённый ответ; другое содержимое с тем же ID возвращает 409. Идентичность файлов между разными batch_id обеспечивает Import.

Данные, результат и undo атомарны в одной PostgreSQL-транзакции. Все apply сериализуются локальным advisory lock; при ошибке БД операция отменяется и HTTP возвращает 503 для повтора того же пакета. Undo хранится в Clients DB; механизм выполнения отката будет отдельным этапом. Сохранённый локальный результат не означает завершения импорта в Operator Work.

Миграция 0002 расширяет схему исходного DDL без изменения ранее созданной миграции. Следующий этап — чтение реестра и расчёты после уточнения контракта с Gateway.
