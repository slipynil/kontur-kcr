# e2e-тесты: установка mise и запуск

Тесты живут в `tests/`. Раннер использует `e2e.config.mjs` из репозитория.
Локальный `e2e.config.ts` имеет приоритет и исключён из Git. Если нужно изменить
настройки только для себя, скопируйте `e2e.config.mjs` в `e2e.config.ts`.
Тесты ролей используют браузерные локаторы и API. Пароли передаются браузеру
через `Secret`. Задачи и версии инструментов заданы в `mise.toml`.

## 1. Установка mise

```bash
curl https://mise.run | sh
```

Бинарь попадает в `~/.local/bin/mise`. Затем подключите его в свой шелл:

```bash
# bash
echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
# zsh
echo 'eval "$(~/.local/bin/mise activate zsh)"' >> ~/.zshrc
```

Альтернативные способы (brew, apt, windows) — https://mise.jdx.dev/install.html.
Для `mise run …` активация не обязательна (mise сам подставляет окружение задачам),
она нужна, чтобы `node`/`poetry`/`npm` были доступны в обычном шелле.

## 2. Инструменты и доверие конфигу

```bash
cd kontur-kcr
mise trust        # один раз после клона: разрешить mise.toml репозитория
mise install      # node 26.12, python 3.14, poetry 2.5.1 (версии из CI)
```

Go-инструменты (`go 1.27.3`, `golangci-lint`, `gomigrate`) описаны в
`services/gateway/mise.toml` и ставятся командой `mise -C services/gateway install`.

## 3. Переменные окружения

Переменные для e2e делятся на два файла: корневой `.env` (из `.env.example`) —
доступ к модели для agent-шагов, `server/.env` — пароли docker-стека.

| Файл / переменная | Обязательность | Назначение |
|---|---|---|
| `.env` в корне: `MODEL_API_KEY`, `OPEN_BASE_URL`, `CHAT_MODEL` | для agent-шагов | Ключ, URL и ID модели OpenAI-совместимого провайдера. Штатный набор проверок запускается без модели |
| `.env` в корне: `MODEL_NAME` | опционально | Отображаемое имя провайдера в AI SDK |
| `server/.env` (из `server/.env.example`) | для задач `stack:*` и `e2e` | Пароли БД/JWT для docker-стека; как настроить — `server/README.md` |
| `APP_URL` | опционально | Адрес фронтенда; дефолт `http://localhost:80` |
| `E2E_ADMIN_PASSWORD` | опционально | Пароль админа: им задача `gateway:seed-admin` сеет админа и e2e-тесты логинятся; дефолт `password123` |
| `E2E_USER_ADMIN_USERNAME` / `E2E_USER_ADMIN_PASSWORD` | опционально | Логин и пароль тестового администратора. Пароль имеет приоритет над `E2E_ADMIN_PASSWORD` |
| `IMAGE_TAG` | опционально | Разовый откат стека: `IMAGE_TAG=<sha> mise run stack:deploy` |

## 4. Запуск

```bash
mise run install   # один раз после клона: npm ci + chromium, go mod download, poetry install ×3
mise run e2e       # поднимет docker-стек, засеет админа (если его нет), дождётся фронтенда и прогонит suite
```

Полный набор задач (`mise tasks` — список, `mise run <task>` — запуск):

| Задача | Что делает |
|---|---|
| `install` / `build` / `test` / `lint` | Зависимости, локальная сборка (gateway + frontend), юнит-тесты и линтеры всех сервисов; подзадачи вида `clients:test`, `gateway:build` — по отдельности |
| `e2e` | `gateway:seed-admin` (→ `stack:up`) → ожидание готовности фронтенда → `npm run test:e2e` |
| `gateway:seed-admin` | Создаёт первого админа в запущенном стеке — `docker compose run` бинаря из образа gateway (важно лишь, что стек поднят). Повторный запуск безопасен: «логин уже занят» не считается ошибкой |
| `stack:up` / `stack:down` / `stack:ps` | Запуск (с локальной сборкой образов)/остановка/статус docker-стека |

Для прогона сценариев администратора и двух операторов на уже запущенном локальном Docker-стеке:

```bash
mise exec -- npm run test:e2e -- --workers 1 tests/access.e2e.ts tests/registry.e2e.ts tests/operator.e2e.ts tests/admin-data.e2e.ts
```

Две операторские учётки создаются через Gateway при первом прогоне и переиспользуются.
Фикстуры создают уникальные синтетические карточки, генерируют небольшие XLSX и
удаляют карточки после тестов. Они находят контейнеры по меткам Compose проекта
`kontur-kcr`. Для другого имени задайте `E2E_COMPOSE_PROJECT`.
Проверяйте порядок отката в одном процессе с одним воркером: операция зависит
от последнего применённого пакета во всём тестовом стеке.

## 5. Отчёты и отладка

- Отчёт прогона — `.e2e/report.json`; по упавшему тесту — трейс
  `.e2e/results/<test>/trace.md` (шаги, экран в момент падения, логи).
- Один тест: `npx e2e run tests/login.e2e.ts:5` (строка его `test(`).
- Прогон без replay-кэша (все agent-цели live): `npx e2e run --no-cache`.
  Кэш живёт в `.e2e/cache/`: подтверждённая проверкой цель воспроизводится без
  вызовов модели, поэтому повторные прогоны дёшевы.
- Upload-тесты требуют реальные выгрузки в `data/` (каталог git-ignored, выгрузки
  не публикуются); без файлов они пропускаются.

## Без mise

Достаточно Node.js ≥ 24.8: `npm install && npx e2e-web install chromium`, стек
поднять руками (`server/README.md`), затем `npm run test:e2e`. mise просто
автоматизирует эти шаги и фиксирует версии.
