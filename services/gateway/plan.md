# Gateway logic — выполненный план работы

Ветка: `gateway/logic` (от `dev`). Область: `services/gateway/`.

Соответствие: spec.md §1.1, §2.1, §3.1; docs/api/public-api.yaml (Auth, Users, x-roles);
docs/api/internal/gateway-identity.yaml; docs/db/ddl/identity.sql; ADR-0001 (тонкий gateway).

## Что сделано

### 1. Identity DB (миграции + хранилище)
- `migrations/000001_init_users.{up,down}.sql` — таблица `users` 1:1 с `docs/db/ddl/identity.sql`.
- `internal/identity` (pgx): `GetByLogin`, `GetByID`, `GetByIDs`, `List` (пагинация + total),
  `Create` (unique → `ErrLoginTaken`); пароли — bcrypt (`internal/identity/passwords.go`).

### 2. Аутентификация и JWT
- `internal/auth`: роли `ADMIN`/`OPERATOR`, идентичность в контексте, JWT HS256
  (`golang-jwt/jwt/v5`), секрет из `JWT_SECRET` (≥32 символов, fail-fast), TTL из `JWT_TTL`
  (по умолчанию 8h), только алгоритм HS256 (защита от подмены alg, включая `none`).
- `POST /api/v1/auth/login`: `{token, token_type: Bearer, expires_in, user}`; единый
  `401 INVALID_CREDENTIALS` для неизвестного логина/неверного пароля/неактивного пользователя;
  сравнение с фиктивным bcrypt-хэшем выравнивает время ответа (анти-перечисление логинов).

### 3. Пользователи
- Публичные: `GET /api/v1/users` (page/page_size → `UserListResponse`), `POST /api/v1/users`
  (валидация: логин ≤64, пароль 8..72, роль из справочника; `201` / `400` / `409
  LOGIN_ALREADY_EXISTS`) — только `ADMIN`.
- Внутренние (docs/api/internal/gateway-identity.yaml): `GET /internal/v1/users/{id}`,
  `GET /internal/v1/users?ids=` (1..100, `display_name` = `login`) — доверенная сеть, без JWT.

### 4. Middlewares (`internal/middlewares`)
- `Authenticate` (Bearer JWT → контекст), `RequireRole` (x-roles; при отсутствии идентичности — 401),
- `RequestLogger` (slog: request_id, method, path, status, duration_ms, user_id, role),
- `Recoverer` (panic → 500 JSON + стек в лог), `CORS` (origins из конфига, preflight до
  аутентификации, при пустом списке CORS выключен).

### 5. Проксирование (`internal/proxy`)
- Reverse proxy per-service (общий Transport с таймаутами), rewrite `/api/v1` → `/internal/v1`,
  `X-User-Id`/`X-User-Role` из проверенного JWT (входящие значения затираются — анти-спуфинг),
  недоступный апстрим → `503 SERVICE_UNAVAILABLE` (детали только в лог).
- Таблица маршрутов по тегам public-api: clients/import/operator-work/assistant с ролями;
  `GET /healthz` без авторизации; 404/405 — JSON в едином формате.

### 6. Сервер и запуск
- `internal/server`: chi-роутер + graceful shutdown (SIGTERM/SIGINT, `context.WithoutCancel`,
  таймауты http.Server). `internal/app`: связывание (конфиг → логгер → токены → БД → сервер),
  DI-опция `WithStore` для тестов; тонкие `cmd/main.go` и `cmd/seed`.
- Конфиг (`configs`): `IDENTITY_DSN`, `JWT_SECRET`, `JWT_TTL`, `CORS_ALLOWED_ORIGINS`,
  `UPSTREAM_TIMEOUT` (+ `.env.example`).
- Бутстрап: `mise run seed-admin` (`ADMIN_LOGIN`/`ADMIN_PASSWORD`), bcrypt-хэш в БД.
- Инфраструктура: `mise.toml` (run/test/lint/fmt/migrate-*/seed-admin), `Dockerfile`,
  `compose.yaml` (identity-db + migrate + gateway по docs/local-development.md), `README.md`.

## Вне scope (отложено намеренно)
- Агрегация карточки клиента из Clients + Operator Work — ADR-0005 п. 8 (после готовности
  Operator Work и согласования объединения выборок).
- `DELETE /api/v1/clients/{kcr_code}` и `DELETE .../snapshots/{quarter}` — нет внутренних
  маршрутов удаления в контракте Import Worker; маршруты не регистрируются (405/404).
- Клиент-карточка сейчас пробрасывается в Clients Service as-is.

## Тесты и проверки
- 70+ тестов без реальной БД: auth (roundtrip/expired/tampered/alg-none/клеймы), handlers
  (fake store), store (pgxmock/v5), middlewares (401/403/CORS/panic/логирование), proxy
  (httptest-апстримы: rewrite путей, заголовки, 503, passthrough), server (полнота таблицы
  маршрутов, RBAC, спуфинг, graceful shutdown), app (DI-подмена хранилища), configs.
- `go test -race -cover ./...` — **91.8%** (порог CI 90%); `golangci-lint run` — **0 issues**.
- E2E вручную с docker compose (postgres:17): миграции, seed-admin, login, CRUD пользователей,
  409/403/401 сценарии, внутренний API, проксирование в живой и недоступный апстрим.

## Замечания по зависимостям
- `pgx` запинен на `v5.11.0`: `pgxmock/v5 v5.2.0` не совместим с `pgx ≥ v5.12`
  (интерфейс `BatchResults`), поднять можно после выхода совместимой версии pgxmock.
