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

## Пагинация списков (2026-09-16)

Все list-эндпоинты принимают `?limit=50&offset=0` (`limit=0` — все строки) и
`?format=paginated`. Без параметров эндпоинт отвечает прежним полным массивом
(совместимость); с любым из параметров — конвертом `{"items": [...], "total": N}`
(`total` — по отфильтрованному запросу). Новые списки делайте через
`PageParams` (`src/core/pagination.py`): `page: PageParams = Depends(page_params)`
и `return page.apply(db, query)` (или `page.apply(db, query, transform=...)`
для пост-обработки строк); в декораторе — `response_model=list[X] | Page[X]`.
На фронте — компонент `PaginatedList` (порция 50, infinite scroll, «Загрузить
ещё», «Загрузить все», счётчик «Показано X из Y»); имена для отображения
кладите в payload (батч-lookup), а не тяните справочник целиком.

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

Внешний ИИ (блок 1, 2026-09-21): Интеграции → Подключения → «Внешний ИИ
(Z.ai / OpenAI / Anthropic)» — base_url API + модель + токен (Fernet).
Активное подключение переключает чат ассистента на внешний провайдер
(OpenAI-совместимый `/chat/completions` или Anthropic `/v1/messages` —
config `style`); эмбеддинги остаются на локальной модели (RAG-векторы).
Ошибка внешнего ИИ → автоматический фолбэк на Ollama. Function calling
не меняется: инструменты в system prompt, ответ-JSON исполняется
существующим циклом (ADR-006: read-only + propose, журнал egress).
Хосты внешнего ИИ в проде — в `CONNECTOR_ALLOWLIST` при strict-режиме.


- **Только локальный Ollama** (данные не покидают контур). Профиль:
  `docker compose --profile ai up -d`, прогрев моделей:
  `docker compose exec api python -m src.modules.ai_agent.pull_models`.
  Дев/тесты — `AI_PROVIDER=llm_mock` (детерминированный мок); переключение
  на живую модель — через `.env` (`AI_PROVIDER=ollama`, модель —
  `AI_CHAT_MODEL`).
- **Память**: 7B-модель требует ~5 ГБ свободной памяти Docker VM; на
  машинах с ≤8 ГБ RAM используйте `AI_CHAT_MODEL=qwen2.5:3b-instruct`.
- **Профиль ai обязателен**: без `--profile ai` контейнер `ollama` не
  поднимается и DNS-имя `ollama` внутри сети compose пропадает —
  connection/провайдер будут падать. Конфигурация live-запуска:
  `docker compose --profile ai up -d` с `AI_PROVIDER=ollama` в `.env`.
- `pull_models` по умолчанию ходит в `http://ollama:11434` (внутри
  compose); запуск с хоста — `OLLAMA_BASE_URL=http://localhost:11434`.
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

## Ресурсы: закупка → склад → сборка → продажа (resources-core)

Ядро управления ресурсами — фичи внутри `mgmt_accounting` (ADR-007):
`features/{inventory, purchasing, sales, production}`, схема общая,
движения товаров — двойной записью (остаток = Σ входов − Σ выходов,
товар возникает/исчезает только в транзитах Поставщик/Клиент/
Производство/Брак). Все деньги/количества в API — Decimal-строками.

Шпаргалка потока (номера — по doc_sequences, префиксы в doc_types):

| Шаг | Документ | Номер | Что делает проведение |
|---|---|---|---|
| 1 | Заказ поставщику `POST /accounting/purchase-orders` | ЗП- | курс заморожен при создании; номер при confirm |
| 2 | Приёмка `POST /accounting/receipts` | ПМ- | движения «Поставщик → склад», серийники, пересчёт средней |
| 3 | Оплата `POST /accounting/purchase-orders/{id}/pay` | СК- | транзакция-расход, категория «Закупки товаров» (авто-seed) |
| 4 | Тех.карта + сборка `POST /accounting/tech-cards`, `/production-orders` | СБ- | списание компонентов по средней (не больше свободного остатка: баланс − резервы продаж), оприходование продукции по себестоимости материалов |
| 5 | Заказ клиента `POST /accounting/sales-orders` | ЗК- | курс при создании; `crm_deal_id` префиллит контрагента из сделки |
| 6 | Отгрузка `POST /accounting/shipments` | ОТ- | движения «склад → Клиент», списание по средней, цифровые коды — FIFO или явным списком, резерв снимается |
| 7 | Оплата `POST /accounting/sales-orders/{id}/pay` | ПК- | транзакция-доход, категория «Продажи» |

Отчёты: `/accounting/reports/purchases`, `/reports/sales` (маржа =
выручка − себестоимость списаний), `/reports/counterparty-balance`
(сальдо = приёмки − оплаты), остатки `GET /stock/balances?on_date=`.

Семантика инвентаризации (`POST /stock/adjustment`) — **полный
факт-список**, не «дельта»: по количественным строкам передаётся
`qty_fact` (факт), по серийным — полный `serial_codes` кодов на
локации. Излишек приходит из транзита «Брак» по текущей средней
(явная `unit_cost` действует только для первого прихода), недостача
уходит в «Брак» по средней; код, не названный в факте, становится
void. Серийные коды — актив: хранятся Fernet-шифрованными (ключ
SECRETS_KEY), уникальность/поиск — по sha256-отпечатку.

Сторно (unpost) приёмок/отгрузок/сборок — парными инверсионными
движениями, только без последующих движений (баланса хватает,
серийники не выданы); средняя себестоимость не откатывается.
События: `acc.inventory.*`, `acc.purchase.*`, `acc.sales.*`,
`acc.production.order.posted` — все в Telegram-белом списке.

## Онлайн-продажи (оплата на сайте → документы → коды сами)

Автоматизация продаж цифровых товаров: покупатель платит на сайте (ЮKassa) —
ERP сама создаёт контрагента, заказ (ЗК-), приходную транзакцию (ПК-),
отгрузку с FIFO-выдачей цифровых кодов и отправляет покупателю письмо/Telegram
с кодами. Спека: `docs/design/sales-automation-spec.md`.

### Как это работает

1. ЮKassa присылает notification `payment.succeeded` на webhook-endpoint ERP.
   **Тело нотификации не доверяется**: ERP повторно запрашивает платёж у API
   ЮKassa с ключами магазина (`verify_by_fetch`) — чужим запросом коды не
   выдать. Дубли гасит журнал `webhook_events` (UNIQUE по connection+event).
2. Платёж нормализуется в `online_payments`: покупатель и строки — из
   `metadata` платежа (`email` + `lines: [{external_id, sku, qty, price}]` —
   сайт кладёт их при создании платежа).
3. По рецепту `sales_flow`: контрагент (find-or-create по email) → заказ →
   confirm → оплата → сверка цен (`price_tolerance`) → отгрузка (выданные
   коды фиксируются в `shipment_lines.serial_ids`) → разовая выдача `deliver`
   → `notify` (письмо с кодами; расшифровка на лету, открытые коды нигде
   не хранятся). Каждый шаг идемпотентен, прогресс — в `flow_runs`: retry
   продолжает с последнего шага, ничего не дублируется.
4. Ошибки → платёж `manual` с причиной (`item_not_mapped`, `price_mismatch`,
   `insufficient_stock`, `delivery_failed`): чините причину (маппинг/
   пополнение склада/SMTP) → `POST /api/v1/integrations/payments/{id}/retry`.
   Деньги при ошибке доставки уже учтены — retry повторит только notify.

### Настройка ЮKassa (пошагово)

1. **Подключение провайдера**:
   `POST /api/v1/integrations/connections`
   `{"name": "ЮKassa", "connector_code": "yookassa",`
   `"credentials": {"shop_id": "<shopId>", "secret_key": "<secretKey>"}}`
   (shopId/secretKey — «Магазин → Ключи API» в кабинете ЮKassa;
   `base_url` в config не указывайте — используется боевой API).
2. **Webhook-endpoint**:
   `POST /api/v1/integrations/webhooks`
   `{"name": "ЮKassa: payment.succeeded", "target_module": "payments", "connection_id": "<id подключения>"}`.
   В кабинете ЮKassa (Магазин → HTTP-уведомления) укажите URL:
   `https://<ваш-домен>/api/v1/integrations/hooks/<endpoint_id>`,
   событие `payment.succeeded`. Заголовок/подпись не нужны — авторизация
   повторным GET статуса (провайдерский режим `connection_id`).
3. **Egress-allowlist**: `api.yookassa.ru` уже в дефолтном списке
   (`Settings.connector_allowlist`); строгий режим запрещает всё вне списка —
   при собственном API-домене провайдера добавьте его. Входящую IP-фильтрацию
   не настраиваем: проверка повторным GET надёжнее списка IP провайдера,
   флуд режет rate limit nginx (30 r/m) на hooks.
4. **Маппинг товаров сайта**:
   `POST /api/v1/integrations/item-mappings`
   `{"connection_id": "<id ЮKassa>", "external_item_id": "site-sku-1", "sku": "DIGI-1", "item_id": "<uuid номенклатуры>"}`.
   Товар должен быть цифровым (`kind=digital`, `tracking=serial`), коды —
   на цифровом складе. `connection_id: null` — глобальный маппинг на все
   подключения.

### Seed-рецепт «Онлайн-продажа цифровых»

Рецепт = триггер + действие. Применение через API (admin-токен; этот же JSON —
пример в OpenAPI у `POST /api/v1/integrations/recipes`):

```json
{
  "name": "Онлайн-продажа цифровых",
  "definition": {
    "trigger_event": "integration.payment.received",
    "action": {
      "type": "sales_flow",
      "connection_id": "<uuid подключения ЮKassa>",
      "config": {
        "account_id": "<uuid счёта зачисления>",
        "price_tolerance": "0",
        "on_no_items": "transaction_only",
        "delivery_channel": "email",
        "smtp_connection_id": "<uuid smtp-подключения>"
      }
    },
    "api_connection_id": "<uuid служебного подключения http_rest>"
  }
}
```

Порядок применения:

1. Служебная учётка для флоу (в учёт ходим публичным API, ADR-001):
   `POST /api/v1/admin/api-tokens` `{"name": "sales-flow", "role": "user"}` → токен.
2. Служебное подключение: `POST /api/v1/integrations/connections`
   `{"name": "sales-flow-api", "connector_code": "http_rest",`
   `"credentials": {"api_key": "<токен>"}, "config": {"base_url": "http://api:8000"}}`
   — флоу сам добавит `/api/v1` и заголовок `X-API-Token`.
3. SMTP-подключение для писем: `POST /api/v1/integrations/connections`
   `{"name": "SMTP", "connector_code": "smtp", "credentials": {"username": "...", "password": "..."},`
   `"config": {"host": "smtp.example.ru", "port": 587, "use_tls": true, "from_email": "sales@yourshop.ru"}}`.
   Для Telegram вместо/вместе с email: `"delivery_channel": "telegram"`
   и `"telegram_chat_id": "<chat>"` (канал v1 — сообщения бота).
4. Создать рецепт (JSON выше) → `POST /api/v1/integrations/recipes/{id}/publish`.
   Рецепт выбирается по `action.connection_id` — у каждого эквайринг-подключения
   может быть свой сценарий.

Проверка: `GET /api/v1/integrations/payments` — статусы и связи
(заказ/транзакция/отгрузка), `GET /payments/{id}` добавляет срез `flow_runs`
(шаг/попытки/ошибка). ro-роль видит замаскированного покупателя.

## Несколько организаций, 2FA, восстановление пароля (multitenancy)

Самообслуживание клиентов (блок 2, 2026-09-22): публичная страница `/signup`
(заявка: организация, имя, email, пароль) → письмо-подтверждение (24 ч,
одноразовый токен, платформенный SMTP; ссылка `/signup/verify?token=…`) →
уведомление платформенному админу → блок «Заявки на подключение» на экране
выбора организации: «Одобрить» (организация + сиды + админ с паролем из
заявки) или «Отклонить». Rate limit 3 заявки/час на email|IP; адресат
уведомлений — Setting `platform_notify_email` (дефолт admin@).


Одна установка Docker — несколько организаций (тенантов). Спека:
`docs/design/multitenancy-spec.md`.

**Организации.** Супер-админ платформы (учётка из первой установки,
`is_platform_admin`) входит → экран выбора организации → работает внутри
выбранной как её админ; платформенный режим (без выбора) — реестр
организаций: создание (организация + админ + временный пароль один раз),
деактивация (вход пользователей запрещён, вебхуки копятся), сброс пароля
любого пользователя. Все данные организации изолированы на уровне
запросов (CompanyScoped): чужие списки пусты, прямой id — 404; sku и
номера документов у каждой организации свои (дубликаты между
организациями допустимы, внутри — нет). Общая ИИ-модель платформы —
без приоритетов (О6).

**2FA (TOTP) для руководителей** (админ организации и супер-админ
платформы; сотрудники — без 2FA): первый вход запускает мастер — QR
(локальный рендер) → код → 10 резервных кодов (показ один раз). До
настройки вход работает с напоминанием; через **7 дней без настройки**
вход блокируется до завершения мастера (поле `totp_setup_deadline`).
Повторный вход — второй шаг: код из приложения или резервный (гасится).
Сброс 2FA (потерян телефон) — только платформа:
`POST /platform/users/{id}/totp/reset`.

**Восстановление пароля.** Забыли пароль: ссылка «Забыли пароль?» на
входе → письмо со ссылкой `/reset-password?token=…` (1 час,
одноразовая; платформенный SMTP — connection `smtp` с company_id NULL).
Руководитель организации может сбросить пароль сотрудника своей
организации (`POST /users/{id}/reset-password`, временный пароль один
раз); свой пароль — только через письмо или платформу (иначе сброс «для
себя» обнулил бы смысл 2FA при захваченной сессии). Платформа сбрасывает
любой: `POST /platform/users/{id}/reset-password`.

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
- **Фронтенд в коммите → пересборка web в регрессе.** Если коммит меняет
  `frontend/**`, в регресс обязательно включается `docker compose build web`
  (сборка гоняет `vue-tsc` и ловит битые импорты/типы) + проверка, что web
  отдаёт новый бандл. Иначе ошибка фронтенда живёт в образе до чужой
  пересборки (урок регресса ai_agent: отсутствующий `put` в api-клиенте).

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
