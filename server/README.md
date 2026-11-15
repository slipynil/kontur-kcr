# Стек на dev-сервере

`server/compose.yaml` собирает всю систему **из готовых образов GHCR** — на сервере
ничего не билдится. Джобы `deploy-to-dev-server` в CI каждого сервиса (после
`docker-build-and-push`) сами копируют compose-файл и миграции gateway на сервер
и выполняют `pull` + `up -d` при пуше в `dev`.

## Одноразовая настройка сервера

Стек живёт в `/srv/kontur` (пути в deploy-джобах CI завязаны на этот каталог).

1. **Docker Engine + compose plugin** (если ещё нет):
   https://docs.docker.com/engine/install/

2. **Логин в GHCR** — образы приватные, без этого `pull` упадёт с `denied`.
   Нужен Classic PAT с правом `read:packages` (Settings → Developer settings →
   Personal access tokens):

   ```bash
   echo <TOKEN> | docker login ghcr.io -u slipynil --password-stdin
   ```

3. **Окружение стека**:

   ```bash
   mkdir -p /srv/kontur/server
   # server/.env.example попадает на сервер первой доставкой CI,
   # но проще создать его вручную заранее:
   editor /srv/kontur/server/.env   # по образцу .env.example из репозитория
   ```

   Все `change-me` заменить: `JWT_SECRET`, `OPERATOR_WORK_SERVICE_TOKEN`,
   пароли четырёх БД и RabbitMQ. Обязательно до первого деплоя: compose
   падает при отсутствующих обязательных переменных (`:?` в compose.yaml).

Первая доставка CI создаст `/srv/kontur/server/compose.yaml`,
`/srv/kontur/services/gateway/migrations/` и поднимет стек. Вручную запускать
`up` не требуется.

## Что развёрнуто

| Сервис | Образ GHCR |
|---|---|
| frontend | `kontur-kcr-frontend` (порт `${FRONTEND_HOST_PORT:-80}` → 3000) |
| gateway | `kontur-kcr-gateway` (порт `${GATEWAY_HOST_PORT:-8080}` → 8080) |
| clients | `kontur-kcr-clients` |
| import-api / import-worker | `kontur-kcr-import-worker` |
| operator-work | `kontur-kcr-operator-work` |
| identity-db, clients-db, import-db, operator-work-db | `postgres:17` |
| rabbitmq | `rabbitmq:4` |

Assistant Service пока не реализован и не развёрнут.

Миграции: Python-сервисы — one-shot контейнеры `alembic upgrade head` из тех же
образов (пересоздаются при смене образа); gateway — `migrate/migrate` с
SQL-файлами из `services/gateway/migrations`, которые CI кладёт рядом с compose.

## Ручной деплой из GitHub Actions

Воркфлоу **Deploy to Dev Server** (`.github/workflows/deploy.yml`) запускается
только по кнопке: **Actions → Deploy to Dev Server → Run workflow** (ветка `dev`).
Он выполняет те же шаги, что автодеплой: scp compose-файла и миграций gateway,
затем `pull` + `up -d`. Общий `concurrency`-group ставит его в очередь с
автоматическими деплоями.

Необязательный вход `image_tag` деплоит указанный тег образов (по умолчанию
`latest`). Тег разовый: он передаётся переменной окружения с приоритетом над
`server/.env` и не меняет файл на сервере. Годятся SHA коммитов, после которых
собрались **все** сервисы (например, большие мерджи); если у какого-то образа
тега нет, `pull` упадёт с `manifest unknown` — это fail-fast, стек не тронут.

## Полезные команды (на сервере)

```bash
cd /srv/kontur

# состояние стека и логи
docker compose --env-file server/.env -f server/compose.yaml ps
docker compose --env-file server/.env -f server/compose.yaml logs -f gateway

# ручной деплой (то же, что делает CI)
docker compose --env-file server/.env -f server/compose.yaml pull
docker compose --env-file server/.env -f server/compose.yaml up -d --remove-orphans
docker image prune -f

# остановка
docker compose --env-file server/.env -f server/compose.yaml down
```

## Откат на предыдущую версию

CI публикует неизменяемый тег по SHA коммита рядом с `latest`. Два способа:

1. **Разово, из UI:** Actions → Deploy to Dev Server → Run workflow →
   `image_tag` = SHA (коммит, после которого собрались все сервисы).
2. **Через `server/.env`:**

```bash
# SHA нужного коммита: Packages → kontur-kcr-<svc> → история версий в GHCR
IMAGE_TAG=<full-commit-sha>
```

и повторите `pull` + `up -d` (или дождитесь следующего деплоя — CI подхватит
значение из `.env`). Откат применяется ко всем сервисам стека сразу; верните
`IMAGE_TAG=latest`, когда CI догонит.

## Замечания

- **Порты не статические:** фронтенд и gateway публикуются на хостовые порты из
  `server/.env` (`FRONTEND_HOST_PORT`, `GATEWAY_HOST_PORT`).
- **`NEXT_PUBLIC_API_URL` запекается в образ фронтенда при сборке** (сейчас
  `http://176.109.100.140`). Смена публичного адреса API — пересборка фронтенда
  с новым `build-arg`, правка одного `.env` недостаточна.
- `server/.env` не коммитится (общий шаблон `.env` в корневом `.gitignore`);
  менять переменные можно прямо на сервере — следующий деплой их подхватит.
