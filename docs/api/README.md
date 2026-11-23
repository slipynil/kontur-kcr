# API Контур КЦР

Контракты OpenAPI 3.1: [публичный API](public-api.yaml) и [внутренние API сервисов](internal/). Вход через Gateway/Identity с Bearer JWT и ролью ADMIN/OPERATOR.

## Изменения

- Для загрузки обязательны файл `.xlsx` и тип импорта. `selected_quarter` и `snapshot_date` передаются только для `SNAPSHOT`. Квартал среза выбирает администратор.
- WAIT_DEADLINE действует до wait_until и сохраняет ответственного через смену квартала. До срока отказ другому оператору — CLIENT_WAIT_DEADLINE_ACTIVE; по сроку при остатке клиент доступен.
- В ответах клиента есть needs_review, inherited_fields, wait_until и квартальный контекст. Остатки учитываются по исходным периодам без двойного счёта.
- Rollback только последнего пакета (IMPORT_NOT_LATEST для другого); удаление клиента ADMIN очищает его работу; квартальное удаление сохраняет остальные кварталы и общую историю. Обычное сохранение истории имеет эти исключения.
- Карточка клиента собирается Gateway из Clients + Operator Work + Identity DB ([ADR-0012](../adr/0012-card-aggregation-on-gateway.md)): новый внутренний `GET /internal/v1/clients/{kcr_code}/work-state` (readClientWorkState) отдаёт рабочие поля текущего цикла; нет цикла — поля null; недоступен Operator Work — публичный ответ 502 BAD_GATEWAY.
- UC-08/10/11 исключены, API очереди дня нет. Переход по сроку — часть модели ожидания.
- ИИ — облачный API; summary/filter-query/chat помечены proposal-pending-ai-scope, конкретный набор ещё не утверждён.
- Хроника взаимодействия клиента читается из Client History ([ADR-0020](../adr/0020-client-history-service.md)): `GET /api/v1/clients/{kcr_code}/events` отдаёт `HistoryEvent` — от новых к старым по дате события, с именем автора и деталями в `payload`; прежние поля сохранены как устаревшие. `POST` остаётся в Operator Work и принимает `COMMENT`. Удаление и откат завершаются после доставки команды очистки в Client History.

## Владение

Gateway — вход и роли; Clients — профили, остатки, происхождение; Import — пакеты/ошибки/undo и координация; Operator Work — циклы, сроки, назначения, операционные события и очистка работы; Client History — хроника взаимодействия клиента (факты и команды очистки приходят из Operator Work); Assistant — интеграция с выбранным позднее облачным провайдером.

Внутренние API доступны в доверенной сети. Gateway устанавливает заголовки для пользовательских запросов, Import Worker передаёт проверенного инициатора для координируемых команд. Операции удаления и импорта идемпотентны. Внешний успех удаления допускается после очистки всех затронутых сервисов. Протокол доставки, повторов и компенсации необходимо завершить до реализации; наличие схем не означает готовность приложения.

Внутренний контракт Clients приведён в [OpenAPI](internal/clients-service.yaml). См. решения о [сравнении остатков](../adr/0005-clients-read-api-and-quarter-dynamics.md), [восстановлении данных](../adr/0006-clients-rollback-and-deletion.md) и [числовых фильтрах](../adr/0007-clients-numeric-filters-and-verification.md).

Import Worker координирует применение и откат пакетов. См. [контракт с Operator Work](import-operator-work-contract.md) и [решение о координаторе](../adr/0007-import-worker-coordinator.md).

Внутренний контракт Client History приведён в [OpenAPI](internal/client-history.yaml): приём фактов и команд очистки, чтение хроники.

[OpenAPI Errors History](internal/errors-history.yaml) описывает атомарный приём ошибок от Import Worker и чтение по КЦР или пакету. По [ADR-0021](../adr/0021-errors-history-service.md) чтение через Gateway предназначено для роли `ADMIN` и подключается отдельным этапом.
