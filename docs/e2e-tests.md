# e2e-тесты: установка mise и запуск

Тесты живут в `tests/*.e2e.ts`, конфиг раннера — `e2e.config.ts`. Тесты сочетают
agentic-шаги (`agent.act` ставит цели на естественном языке, модель сама водит UI)
с точными локатор-проверками; пароль учётки передаётся как `Secret` и в логи/модель
не попадает. Инструменты и задачи оркестрированы через [mise](https://mise.jdx.dev/)
(конфиг `mise.toml` в корне), версии совпадают с CI.

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

| Файл / переменная | Обязательность | Назначение |
|---|---|---|
| `.env` в корне: `ZAI_API_KEY` | для agent-шагов | Ключ модели (OpenAI-совместимый endpoint задан в `e2e.config.ts`). Раннер сам не читает `.env` — его подгружает `e2e.config.ts` через `process.loadEnvFile()`. Без ключа agentic-тесты упадут на первом вызове модели |
| `server/.env` (из `server/.env.example`) | для задач `stack:*` и `e2e` | Пароли БД/JWT для docker-стека; как настроить — `server/README.md` |
| `APP_URL` | опционально | Адрес фронтенда; дефолт `http://localhost:80` |
| `E2E_ADMIN_PASSWORD` | опционально | Пароль админа: им задача `gateway:seed-admin` сеет админа и e2e-тесты логинятся; дефолт `password123` |

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
