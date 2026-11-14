# Gateway / Identity Service

Единая точка входа «Контур КЦР» (Go): аутентификация и авторизация (JWT, роли
`ADMIN`/`OPERATOR`), реестр пользователей, проксирование REST-запросов во внутренние
микросервисы, CORS и таймауты — spec.md §1.1, §3.1, docs/api/public-api.yaml.

## Структура

```
cmd/            точка входа gateway и seed-admin
configs/        конфигурация (.env + переменные окружения, viper)
internal/
  app/          связывание конфигурации, БД и сервера
  auth/         роли, JWT HS256, идентичность запроса
  identity/     Identity DB (pgx), обработчики auth/users, внутренний API
  httpx/        общий формат ошибок публичного API
  middlewares/  JWT-аутентификация, RBAC, slog-логирование, recover, CORS
  proxy/        reverse proxy /api/v1 -> /internal/v1 с заголовками X-User-*
  server/       chi-роутер и graceful shutdown
migrations/     golang-migrate (таблица users, docs/db/ddl/identity.sql)
```

## Маршруты

Собственные (без прокси):

- `POST /api/v1/auth/login` — выдача JWT;
- `GET/POST /api/v1/users` — реестр пользователей, только `ADMIN`;
- `GET /internal/v1/users/{id}`, `GET /internal/v1/users?ids=` — данные пользователей
  для внутренних сервисов (docs/api/internal/gateway-identity.yaml);
- `GET /healthz`.

Проксируемые (заголовки `X-User-Id`, `X-User-Role` проставляются gateway, входящие
значения затираются; путь переписывается `/api/v1` → `/internal/v1`):

| Маршрут | Сервис | Роли |
|---|---|---|
| `GET /api/v1/clients`, `GET /api/v1/clients/{kcr_code}`, `GET .../snapshots/history` | Clients | ADMIN, OPERATOR |
| `GET/POST /api/v1/clients/{kcr_code}/events` | Operator Work | ADMIN, OPERATOR |
| `POST /api/v1/clients/{kcr_code}/take`, `/api/v1/reassignment-notifications*` | Operator Work | OPERATOR |
| `POST /api/v1/imports`, `GET /api/v1/imports/{batch_id}`, `POST .../rollback` | Import Worker | ADMIN |
| `/api/v1/assistant/*` | Assistant | ADMIN, OPERATOR |

Агрегация карточки клиента (ADR-0005, п. 8) и публичные маршруты удаления клиентов
(нет внутренних контрактов удаления у Import) будут добавлены отдельно.

## Локальный запуск

Задачи — через mise (см. `mise.toml`):

```bash
cp .env.example .env          # задайте JWT_SECRET
docker compose up -d identity-db migrate   # Identity DB + миграции
mise run migrate-up           # либо миграции вручную
ADMIN_LOGIN=admin ADMIN_PASSWORD=password123 mise run seed-admin
mise run run                  # gateway на :8080
```

Python-сервисы адресуются переменными `*_ENDPOINT` (порты —
docs/local-development.md). Контейнерный `gateway` из compose по умолчанию
использует имена сервисов из внутренних контрактов (общая docker-сеть).

## Проверки

```bash
mise run test   # go test -race -cover ./...
mise run lint   # golangci-lint run
```

Первый администратор создаётся задачей `seed-admin` (bcrypt-хэш, пароль нигде
не сохраняется в открытом виде).
