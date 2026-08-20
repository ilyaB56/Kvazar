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
ADR-003 «Денежные величины и мультивалютность».

## Дорожная карта

1. ~~Ядро + интеграционная платформа~~ (этот каркас)
2. ~~`mgmt_accounting` — управленческий учёт~~ (v1: справочники, транзакции,
   сторно, периоды, курсы, cashflow)
3. `ai_agent` — Ollama + RAG (pgvector), tool-use поверх коннекторов SDK
4. `mini_crm` — CRM
5. Фронтенд Vue 3: магазин интеграций, no-code конструктор, дашборды
