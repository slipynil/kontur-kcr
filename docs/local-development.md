# Локальный запуск и порты сервисов

Единая конвенция настройки портов для локальной разработки. Цель — каждый сервис
поднимается своим `docker compose up` без правок конфигов и без конфликтов портов,
а Gateway адресует сервисы через переменные своего `.env`.

## Принципы

- **Порт в контейнере** задаётся env-переменной сервиса (дефолт `8000` у
  Python-сервисов, `8080` у Gateway). Дефолты совпадают с адресами в
  спецификациях `docs/api/internal/*.yaml` (`http://clients-service:8000` и т. п.),
  поэтому менять их нужно редко.
- **Хостовый порт** задаётся отдельной переменной `*_HOST_PORT` в `.env`
  директории сервиса (копируется из `.env.example`). Дефолты уникальны — все
  сервисы поднимаются одновременно.
- **Gateway** читает готовые эндпоинты (`CLIENTS_ENDPOINT`, …) из
  `services/gateway/.env`; реальные переменные окружения имеют приоритет над
  файлом (`viper.AutomaticEnv`).

## Таблица портов

| Сервис | ENV порта в контейнере | Дефолт | ENV хостового порта | Дефолт | Эндпоинт в gateway `.env` |
|---|---|---|---|---|---|
| Gateway / Identity | `GATEWAY_PORT` | `8080` | — | `8080` | — |
| Clients | `CLIENTS_PORT` | `8000` | `CLIENTS_HOST_PORT` | `8001` | `CLIENTS_ENDPOINT=http://127.0.0.1:8001` |
| Import API | `IMPORT_API_PORT` | `8000` | `IMPORT_API_HOST_PORT` | `8002` | `IMPORT_API_ENDPOINT=http://127.0.0.1:8002` |
| Operator Work | `OPERATOR_WORK_PORT` | `8000` | `OPERATOR_WORK_HOST_PORT` | `8003` | `OPERATOR_WORK_ENDPOINT=http://127.0.0.1:8003` |
| Assistant | `ASSISTANT_PORT` | `8000` | `ASSISTANT_HOST_PORT` | `8004` | `ASSISTANT_ENDPOINT=http://127.0.0.1:8004` |

## Как поднять локально

```bash
# в директории каждого сервиса (clients, import-worker, operator-work)
cp .env.example .env   # при необходимости переопределите порты
docker compose up

# gateway
cd services/gateway
cp .env.example .env
```

## Замечания

- Хостовой порт живёт в двух местах: `.env` сервиса (маппинг compose) и `.env`
  gateway (эндпоинт). Это цена отдельного compose-файла на сервис; при смене
  порта обновляйте оба места по этой таблице.
- Import Worker — это два процесса: `import-api` (слушает порт) и consumer
  очереди RabbitMQ (портов не имеет).
- Assistant Service ещё не реализован; его значения зарезервированы.
- `EXPOSE` в Dockerfile документирует дефолт; фактический порт определяет
  env-переменная в runtime (CMD в shell-форме раскрывает `${...}`).
- Переменные окружения процесса имеют приоритет над `.env`-файлами.
