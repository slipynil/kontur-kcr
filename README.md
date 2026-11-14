# Контур КЦР — контроль передачи оригиналов документов

Веб-сервис объединяет квартальные данные о документах и работу операторов. Ключ клиента — код КЦР; одному ИНН могут соответствовать несколько кодов.

## Текущая стадия

Актуальная спецификация — [spec.md 1.5](spec.md); изменения требований зафиксированы в [ADR-0002](docs/adr/0002-quarter-imports-deadlines-and-data-removal.md).

## Документы

- [Техническая спецификация](spec.md)
- [Актуальные юзкейсы](docs/use-cases.md)
- [API](docs/api/README.md), [модель данных](docs/db/README.md)
- [Локальный запуск и порты сервисов](docs/local-development.md)
- [Архитектура C4 L2](docs/c4-l2-container-diagram.html), [ADR](docs/adr/)
- [Исходное ТЗ, обновлённое по разговору](docs/requirements/ТЗ.md)
- Исходные выгрузки и комментарии заказчика хранятся локально; актуальные решения отражены в [spec.md](spec.md) и [use-cases](docs/use-cases.md).

## Архитектура

Микросервисная архитектура: каждый сервис — отдельная PostgreSQL БД, физические FK существуют только внутри одной БД, связи между сервисами логические. Полная диаграмма — [C4 L2](docs/c4-l2-container-diagram.html), решения — [ADR](docs/adr/).

| Сервис | Язык | Зона ответственности |
|---|---|---|
| Gateway / Identity | Go | Единственная точка входа: аутентификация и авторизация (JWT), роли `ADMIN`/`OPERATOR`, проксирование запросов во внутренние сервисы, CORS и таймауты |
| Clients Service | Python | Реестр клиентов (Код КЦР, ИНН, теги), квартальные срезы и журнал истории изменения статуса передачи документов, автоматический расчёт динамики, поиск по Коду КЦР и ИНН |
| Import Worker | Python | Фоновая обработка Excel-выгрузок: парсинг и валидация по правилам КЦР, дедупликация, отчёт об импорте, откат последнего пакета |
| Operator Work Service | Python | Атомарное закрепление клиентов за операторами, этапы работы, лента событий карточки (звонки, комментарии, письма, претензии), пулы под квартальный обзвон |
| Assistant Service | Python | ИИ-ассистент через облачный API внешнего провайдера; функции согласуются отдельно, ответы опираются на факты сервиса |

Взаимодействие: Gateway агрегирует данные Clients Service и Operator Work Service в публичной карточке клиента; импорт идёт асинхронно через RabbitMQ. LLM — облачный внешний API; MCP и конкретные инструменты ИИ остаются предложениями до согласования функций.

## Сервисы и CI-воркфлоу

| Воркфлоу | Путь | Стек |
|---|---|---|
| gateway-pr-checks | `services/gateway` | Go 1.27, golangci-lint |
| clients-service-pr-checks | `services/clients` | Python 3.14, Poetry, Ruff |
| import-worker-pr-checks | `services/import-worker` | Python 3.14, Poetry, Ruff |
| operator-work-service-pr-checks | `services/operator-work` | Python 3.14, Poetry, Ruff |
| assistant-service-pr-checks | `services/assistant` | Python 3.14, Poetry, Ruff |
| frontend-pr-checks | `frontend` | Node.js 26.12, Next.js, ESLint, Jest |
