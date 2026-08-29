# Custom ERP

Собственная ERP-система с нуля: модульный монолит с плагинами и интеграционной
платформой (банки, CRM, нейросети, госсервисы, любые REST/API-сервисы).

## Стек

Python 3.11+ · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 (+pgvector) ·
Redis + Celery · Docker Compose

## Быстрый старт

```bash
cp .env.example .env        # при локальном запуске без Docker
docker compose up --build
```

Поднимутся: БД (5432), Redis (6379), API (http://localhost:8000, Swagger — /docs),
Celery worker и beat. Миграции применяются автоматически, создаётся админ:

```
admin@example.com / admin12345   (смените пароль!)
```

## Архитектура

- **Ядро** (`src/core`, схема `erp_core`): auth (JWT), RBAC, компании/контакты,
  настройки, аудит, событийная шина (outbox + обработчики), реестр модулей.
- **Модули** (`src/modules/<name>`, своя схема БД): зависят только от контрактов
  ядра (`core.contracts`), общаются между собой через события.
- **Интеграционная платформа** (`src/modules/integrations`, схема `integrations`):
  - Connector SDK: `BaseConnector` — напишите адаптер любого сервиса;
  - встроенные коннекторы: универсальный REST/HTTP и шаблон банковского API;
  - подключения с шифрованием секретов (Fernet);
  - входящие webhooks (`/api/v1/integrations/hooks/{id}`) и исходящие события;
  - sync jobs (cron, ретраи, журнал `sync_runs`);
  - recipes — каркас no-code конструктора и магазина интеграций.
- **Управленческий учёт** (`src/modules/mgmt_accounting`, схема `mgmt_accounting`,
  API `/api/v1/accounting`): счета/статьи/контрагенты, транзакции с жизненным
  циклом черновик → проведение → сторно, нумерация `ПК/СК/ПР/СТ-ГГГГ-NNNNN`,
  периоды (закрытие/переоткрытие), мультивалютность с заморозкой курса на дату
  операции (ADR-003), отчёт «движение денег», журнал версий (`erp_core.record_versions`).
- **Фронтенд** (`frontend/`): Vue 3 + Vite + TS + Pinia + Element Plus + vue-i18n (ru).
  fetch-обёртка с Bearer и авто-refresh (401 → refresh → повтор → logout), типы DTO
  в `frontend/src/api/types.ts`, все строки через i18n-ключи, деньги строками (ADR-003).
  Экраны: логин с guard'ами, «Подключения» (диалог по config_schema, проверка связи,
  webhooks), «Синхронизации» (jobs + журнал runs). Внешних CDN нет — все ассеты
  из бандла (ADR-001).

## Как подключить внешний сервис (пример)

1. `GET /api/v1/integrations/connectors` — список типов коннекторов.
2. `POST /api/v1/integrations/connections` — подключение:
   `{"name": "Мой банк", "connector_code": "http_rest",
     "credentials": {"api_key": "..."}, "config": {"base_url": "https://api.bank.ru"}}`
3. `POST /api/v1/integrations/connections/{id}/test` — проверка.
4. `POST /api/v1/integrations/sync-jobs` — регулярная синхронизация (cron), затем
   `POST /api/v1/integrations/sync-jobs/{id}/run`.

Для приёма событий от сервиса: `POST /api/v1/integrations/webhooks` → получите URL
и токен, отдайте их внешней системе.

## Как добавить новый модуль

1. Создайте пакет `src/modules/<name>/` с `manifest.py` (Manifest: имя, версия,
   схема БД, зависимости, роутеры, обработчики событий).
2. Зарегистрируйте его в `src/core/plugins.py` → `MANIFESTS`.
3. Напишите миграцию Alembic (своя схема, downgrade обязателен).
4. Модуль не трогает чужие схемы и общается с другими только через события/API.

## Тесты

```bash
docker compose exec api pytest tests/test_unit.py      # без БД
docker compose up -d && pytest tests/                   # integration-тесты против API
```

## Фронтенд: dev и prod

Node.js на хосте не нужен — весь npm-цикл в Docker.

```bash
# dev-сервер (http://localhost:5173, прокси /api → api:8000)
docker run -d --name erp-frontend-dev --network erp_default -p 5173:5173 \
  -e API_TARGET=http://api:8000 -v "%cd%/frontend:/app" \
  -v erp_frontend_node_modules:/app/node_modules -w /app node:20-alpine \
  sh -c "npm install && npm run dev -- --host 0.0.0.0"

# prod: nginx отдаёт бандл и проксирует /api (http://localhost:8080)
docker compose up -d --build web
```

Внимание (Docker Desktop + bind mount): изменения файлов фронтенда могут не
подхватываться vite по HMR — при странном поведении перезапустите dev-контейнер.

## ИИ-агент (ADR-006)

- **Только локальный Ollama** (данные не покидают контур). Профиль:
  `docker compose --profile ai up -d`, прогрев моделей:
  `docker compose exec api python -m src.modules.ai_agent.pull_models`.
  Дев/тесты — `AI_PROVIDER=llm_mock` (детерминированный мок).
- Внешние LLM выключены (`ENABLE_EXTERNAL_LLM=false`); включение —
  отдельным решением (поправка ADR-006).
- **Режим агента**: чтение — сразу (отчёты, поиск, RAG), запись — только
  через предложения (Proposals) с подтверждением; пользовательская
  настройка «Автоприменение» (выкл. по умолчанию) применяет предложения
  ИИ из чата сразу со статусом `auto_applied`; предложения по выпискам
  всегда требуют явного одобрения.
- Полное логирование: диалоги (chat_sessions/chat_messages), предложения
  (proposals), ключевые действия в events_log. Секреты (\*.pem/\*.key/.env)
  в документы не загружаются.

## Безопасность P0 (краткая шпаргалка)

- **Роли**: mutating-эндпоинты — минимум `user` (readonly — только GET);
  конфигурация интеграций и админ-операции — `admin`.
- **Логин**: rate limit — 5 неудачных попыток в минуту с одного IP → 429
  с `Retry-After`; успешный вход сбрасывает счётчик. Redis недоступен →
  вход работает (fail-open).
- **Сессии**: смена пароля (`POST /api/v1/auth/change-password`) инвалидирует
  ВСЕ токены пользователя (требуется повторный вход). `POST /api/v1/auth/logout`
  отзывает refresh-токен; access-токен живёт оставшиеся минуты и умирает сам.
- **Пароли**: минимум 8 символов, буква и цифра, запрет топ-500 частых (422
  с описанием правил).
- **HTTPS (коробка)**: self-signed сертификат одной командой, затем поднять
  web с TLS:

  ```bash
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -subj "/CN=localhost" \
    -keyout deploy/certs/tls.key -out deploy/certs/tls.crt
  WEB_TLS=1 docker compose up -d --build web
  # http://localhost → 301 → https://localhost (HSTS включён)
  ```

  Без `WEB_TLS` сервис web работает как раньше (8080, HTTP). Порт API
  опубликован только на `127.0.0.1:8000` — наружу только через web-прокси.

## Обновление и бэкапы (фаза 1, ADR-004)

Уточнение к ADR-004: в фазе 1 **применение обновления — командой на хосте**
(`python deploy/update.py`); кнопка в UI показывает версию/changelog и
запускает *проверку*. Полный one-click — фаза 2 (нужен агент с docker-socket).

### Бэкапы

- Ежедневно в `BACKUP_SCHEDULE` (beat) + вручную из UI (админ) +
  автоматически перед каждым обновлением (`pre_update`).
- Шифрование Fernet по `BACKUP_KEY` (отдельный от `SECRETS_KEY`!);
  retention `BACKUP_RETENTION` последних; проверка целостности
  (расшифровка + sha256 + test-restore + sanity) — кнопкой и ежемесячно.
- Восстановление вручную (прод):

  ```bash
  docker compose stop api worker beat web
  docker compose run --rm api python -m src.backup restore \
    --backup-id <id из GET /api/v1/system/backups> --target erp --drop
  docker compose up -d
  ```

  Откат данными через `alembic downgrade` на живой БД запрещён (ADR-004) —
  только восстановление бэкапа.

### Релизы (наша сторона)

  ```bash
  py tools/release.py keygen --private <путь вне репо> --public deploy/keys/update-public.pem
  py tools/release.py build --version 0.1.1 --changelog-file CHANGES.md
  ```

  Манифест (версия, канал, changelog, min_supported, дайджесты образов) +
  подпись Ed25519. Приватный ключ — офлайн, `RELEASE_KEY_PATH`.

### Обновление на хосте клиента

  ```bash
  UPDATE_MANIFEST_URL=https://…/manifest.json python deploy/update.py
  ```

  Порядок: подпись и min_supported проверяются ДО любых действий →
  pre-flight (место ≥2×БД, стек зелёный) → обязательный pre_update-бэкап →
  образы по дайджестам (несовпадение — стоп) → `up -d` (миграции применяет
  api транзакционно) → health-check до 120 с. Провал — авто-откат:
  восстановление бэкапа + прежние образы + повторный health-check; журнал —
  `deploy/update.log`, события `system.updated`/`system.rollback` в аудите.

  Проверка наличия обновлений: beat раз в 24 ч + кнопка «Проверить сейчас»
  (раздел «Обновления и бэкапы», админ); дев-режим — манифест-файл
  `deploy/test-manifest.json` (gen-test-manifest), file:// URL.


## С чего начать новую сессию разработки

Репозиторий — единственный источник правды; вся история решений в `docs/`.
Порядок чтения для нового агента/разработчика:

1. Этот README (архитектура, правила кода, запуск).
2. `docs/decisions-registry.md` — реестр всех решений и долгов.
3. `docs/adr/` — обязательные архитектурные решения (ADR-001/002/003).
4. `docs/design/` — спецификации модулей (учёт, фронтенд).
5. `docs/security-plan.md` — план безопасности (P0/P1/P2).

Правила: спека → код → приёмка (smoke + pytest + ruff). Неоднозначности
спеки — вопросы до кодинга. Новые сквозные решения — только через ADR.

## Правила кода

- **Сеть — только в коннекторах.** Импорты сетевых библиотек (`httpx`, `requests`,
  `urllib.request`, `aiohttp`, `socket` и аналогов) разрешены **только** в
  `src/modules/integrations/connectors/` (ADR-001). Бизнес-модули не открывают
  сетевых соединений никогда — внешний мир приходит к ним событиями шины.
- Правило проверяется линтером: `ruff check src` (правило TID251, banned-api,
  настроено в `pyproject.toml`).
- **Нарушение — блокирующее на ревью**: PR с сетевым импортом вне коннекторов
  не принимается, независимо от «зелёного» CI.

## Правила (из техплана, сохранены)

- Модули общаются только через события и публичный API, без прямых импортов.
- Миграции обратимые; деструктивные изменения запрещены без альтернативного пути.
- Секреты подключений хранятся только в зашифрованном виде.

## Архитектурные решения

Принятые решения фиксируются в журнале [docs/adr/](docs/adr/README.md):
ADR-001 «Доступ к сети — только через интеграционный модуль»,
ADR-002 «Версионирование API (`/api/v1`)»,
ADR-003 «Денежные величины и мультивалютность»,
ADR-004 «Обновления коробочной версии»,
ADR-005 «Внутренняя структура модулей».

## Дорожная карта

1. ~~Ядро + интеграционная платформа~~ (этот каркас)
2. ~~`mgmt_accounting` — управленческий учёт~~ (v1: справочники, транзакции,
   сторно, периоды, курсы, cashflow)
3. `ai_agent` — Ollama + RAG (pgvector), tool-use поверх коннекторов SDK
4. `mini_crm` — CRM
5. ~~Фронтенд Vue 3~~ (каркас v1: логин, подключения, синхронизации; экраны
   учёта и конструктор рецептов — следующие)
