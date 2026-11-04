# API «Контур КЦР»

Спецификации API системы в формате **OpenAPI 3.1 (YAML)**.

Два контура:

1. **Публичное API** — выходит наружу, к Web App (SPA). Единственная точка входа —
   nginx → Gateway / Identity Service (Go), который проверяет Bearer JWT и роль и
   проксирует запросы во внутренние микросервисы (ADR-0001, C4 L2).
2. **Внутренние API** — контракты между микросервисами; доступны только в закрытой
   сети, из внешнего контура недоступны.

## Файлы

| Файл | Контур | Сервис | Статус |
|------|--------|--------|--------|
| [`public-api.yaml`](public-api.yaml) | Публичный | Все, через Gateway | Эндпоинты — строго по `spec.md` §3 |
| [`internal/gateway-identity.yaml`](internal/gateway-identity.yaml) | Внутренний | Gateway / Identity (Go) | Предложение |
| [`internal/import-worker.yaml`](internal/import-worker.yaml) | Внутренний | Import API + Import Worker (Python) | Частично по `spec.md` (rollback), остальное — предложение |
| [`internal/clients-service.yaml`](internal/clients-service.yaml) | Внутренний | Clients Service (Python) | Предложение |
| [`internal/operator-work-service.yaml`](internal/operator-work-service.yaml) | Внутренний | Operator Work Service (Python) | Частично по `spec.md` (take/events/today), остальное — предложение |
| [`internal/assistant-service.yaml`](internal/assistant-service.yaml) | Внутренний | Assistant Service (Python) | Эндпоинты — по `spec.md` §3.4, пути — предложение |

Публичные эндпоинты и сервис-исполнитель:

| Публичный эндпоинт | Сервис |
|--------------------|--------|
| `POST /api/v1/auth/login`, `GET/POST /api/v1/users` | Gateway / Identity |
| `POST /api/v1/imports/{batch_id}/rollback` | Import Worker |
| `GET /api/v1/clients`, `GET /api/v1/clients/{kcr_code}`, `GET /api/v1/clients/{kcr_code}/snapshots/history` | Clients Service |
| `GET/POST /api/v1/clients/{kcr_code}/events`, `POST /api/v1/clients/{kcr_code}/take`, `GET /api/v1/operator/today` | Operator Work Service |
| `GET /api/v1/assistant/summary/{kcr_code}`, `POST /api/v1/assistant/filter-query`, `POST /api/v1/assistant/chat` | Assistant Service |

## Общие конвенции

- **Аутентификация (публичный контур):** `Authorization: Bearer <JWT>`; роли
  `ADMIN` / `OPERATOR`; доступность операции отмечена расширением `x-roles`.
- **Идентификация (внутренний контур):** Gateway проставляет заголовки `X-User-Id`,
  `X-User-Role`, `X-Request-Id`; внутренние сервисы доверяют им только в закрытой
  сети (контракт — в `internal/gateway-identity.yaml`).
- **Ошибки:** единая схема `Error` с машиночитаемым `code` (каталог — в
  `public-api.yaml`).
- **Пагинация:** `page` / `page_size` (до 100), ответ `items` + `total`.
- **Идентификаторы:** бизнес-ключ клиента — `kcr_code` (Код КЦР); пакет импорта и
  события — UUID.
- **Терминология:** слова «долг», «задолженность», «должник» запрещены
  (`spec.md` §1.2); только «непереданные документы».

## Принципы

- **Zero External Data Leakage** (`spec.md` §1.2): данные клиентов не передаются во
  внешние сервисы; ассистент работает только на локальной модели.
- **Одна задача — один PR** (`AGENTS.md`): контракты с пометкой `[Предложение]`
  финализируются в соответствующих PR (номера PR — в `spec.md` §5).
- Каждый внутренний файл содержит разделы «Кто вызывает», «Куда вызывает сам» и
  «Открытые вопросы» — пробелы, не зафиксированные в `spec.md`, собраны там, а не
  молча дописаны в контракты.

## Как посмотреть

Любой просмотрщик OpenAPI 3.1, например:

- [Swagger UI](https://editor.swagger.io/) — открыть файл,
- [Redoc](https://redocly.github.io/redoc/) — `redocly preview-docs <файл>`.

## Связанные документы

- Спецификация системы: [`../../spec.md`](../../spec.md)
- Архитектурное решение: [`../adr/0001-split-system-into-microservices-by-domain.md`](../adr/0001-split-system-into-microservices-by-domain.md)
- Диаграмма контейнеров: [`../c4-l2-container-diagram.html`](../c4-l2-container-diagram.html)
