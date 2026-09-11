# Спека: автоматизация продаж через онлайн-оплату (эквайринг → документы → автовыдача)

Статус: **ревью архитектурного чата пройдено 2026-09-10; развилки §12
закрыты (см. ниже); к утверждению основателя и запуску после гейта
пилотов** · Дата: 2026-09-10 ·
Автор: erp-analyst. Источник: отзыв основателя 2026-09-10 п.2
(decisions-registry): «убрать ручной ввод — оплата на сайте → данные сами
в ERP → процессы срабатывают сами».

## 1. Контекст и проблема

Продавец цифровых продуктов продаёт коды на своём сайте через эквайринг
(ЮKassa/Т-Касса/Robokassa). Сегодня каждая продажа = ручной ввод: увидеть
оплату, создать заказ клиента, провести оплату, отгрузить (выдать код),
отправить код покупателю. Это часы рутины, ошибки копипаста и «забыл
отправить код» — главный источник поддержки.

Целевой сценарий («ключевой», слова основателя): покупатель оплатил
на сайте → ERP **сама** создаёт подтверждённый заказ клиента, входящую
транзакцию-оплату, отгружает цифровой товар FIFO-выдачей кода и отправляет
код покупателю (email/Telegram). Ноль ручной работы.

Границы задачи: только онлайн-оплата как **триггер** продаж. Сами деньги,
склад, заказы, отгрузки уже реализованы (ядро ресурсов A–E,
resources-core-spec). Эта спека — про мост «внешний платёж → документы
учёта → доставка кода», целиком внутри модуля `integrations` (ADR-001)
плюс минимальные сервисные операции в `mgmt_accounting`.

## 2. As-is (чем опираемся)

Реализовано и переиспользуется без изменений:

- **Продажи**: `sales_orders` (draft→confirmed→…→shipped, номер ЗК- при
  confirm, курс заморожен при создании ADR-003), отгрузки с
  FIFO-автовыбором серийников и сторно, оплата `POST /sales-orders/{id}/pay`
  (категория «Продажи», контрагент наследуется) —
  `src/modules/mgmt_accounting/features/sales/models.py`,
  `router.py`, `service.py`. Коды хранятся Fernet-шифрованными и **не
  светятся в API** (инвентарь, этап A); известная полировка: FIFO-выданные
  коды не пишутся в `shipment_lines.serial_codes` (decisions-registry).
- **Connector SDK**: `BaseConnector` (test_connection/fetch/push/
  verify_webhook), реестр, `Connection` с Fernet-секретами —
  `src/modules/integrations/sdk.py`, `connectors/builtin.py`,
  `crypto.py`.
- **Вебхуки**: публичный приём `/api/v1/integrations/hooks/{id}` (ADR-002),
  сейчас авторизация **только X-ERP-Token** (`router.py:165–188`);
  `HttpRestConnector.verify_webhook` (HMAC X-Signature) существует, но
  приёмником **не вызывается** (докстринг обещает — код не делает;
  первый кандидат на исправление в этапе A). Журнала входящих и
  идемпотентности нет — повторный вебхук = повторное событие.
- **Рецепты**: `recipes.definition = {trigger_event, action:{type:
  'api_call', …}}`, исполнение в воркере через connection `http_rest`
  с `X-API-Token` — `recipes_executor.py`, `tasks.py`. Только один шаг
  `api_call`; многошаговых сценариев нет.
- **Служебные токены** `erp_core.api_tokens` (showcase-chain, этап A) и
  принцип «интеграции создают документы учёта через публичный API от
  служебной учётки» (mgmt-accounting-spec, «Сквозная цепочка»).
- **Уведомления**: Telegram-коннектор + `notification_rules` с белым
  списком `NOTIFY_EVENTS` (`notify.py`). Email-коннектора нет.
- **P1 security-plan (пп. 7–8)**: allowlist доменов + журнал исходящих;
  rate limit на `/hooks/{id}` + идемпотентная обработка — заявлены, не
  реализованы. Данная спека — их первый потребитель.

## 3. To-be: принципы и целевой процесс

### 3.1 Принципы

1. **Сеть — только в коннекторах** (ADR-001): ЮKassa/Т-Касса/Robokassa,
   Telegram, SMTP — только `connectors/`. Оркестратор документов ходит в
   учёт через публичный API `/api/v1` с X-API-Token.
2. **Деньги не ждут товара**: факт оплаты отражается в учёте всегда и
   сразу (входящая транзакция). Товарные шаги (заказ/отгрузка/выдача) при
   проблеме останавливаются в «ручную очередь», но деньги уже учтены.
3. **Идемпотентность на каждом уровне**: ключ вебхука → журнал
   `webhook_events` (дубли молча пропускаются); ключ платежа →
   `online_payments` (UNIQUE provider+payment_id); флоу — шаговый
   `flow_runs` с продолжением с последнего успешного шага, а не «с нуля».
4. **Недоверие входящему телу**: тело нотификации — только намёк. Истина —
   повторный запрос статуса платежа через API провайдера
   (`verify_by_fetch`) и/или проверка подписи (`verify_webhook`).
5. **Один оркестратор, много провайдеров**: адаптер-коннектор
   нормализует платёж (единый словарь), оркестратор `sales_flow` один на
   всех. Новый провайдер = новый адаптер, не новая логика.
6. **Коды — актив**: расшифрованные коды покидают систему только через
   операцию доставки покупателю (шаг deliver), с аудитом; в событиях шины
   и логах — никогда.
7. **ПДн покупателя — минимум и защита** (ADR-006): email/телефон нужны
   для контрагента и доставки; в события шины и Telegram-шаблоны не
   попадают; ИИ их не видит.
8. Деньги/количества — Decimal-строки (ADR-003); все новые таблицы —
   схема `integrations`; кросс-модульные ссылки — UUID без FK (как
   `crm_deal_id`).

### 3.2 Целевой процесс (happy path)

1. **Приём**: ЮKassa шлёт `payment.succeeded` на
   `/api/v1/integrations/hooks/{id}` (endpoint привязан к connection
   «ЮKassa»). Приёмник: проверка коннектором (verify_by_fetch — см.
   §3.4) → запись в `webhook_events` (дубликат по external_key → 200
   «duplicate», без обработки) → нормализация в `online_payments` →
   событие `integration.payment.received` → ответ 202. Ответ 202 всегда
   после записи в журнал (ретраи провайдера гасятся идемпотентностью).
2. **Запуск флоу** (Celery, воркер): рецепт с `trigger_event =
   integration.payment.received` и action `sales_flow`.
3. **Контрагент**: find-or-create по email/телефону (новый эндпоинт
   учёта, §5.2) — контакт + контрагент.
4. **Заказ**: маппинг строк платежа через `item_mappings` (§4) →
   `POST /accounting/sales-orders` (цены — из платежа) → `confirm`.
5. **Оплата**: `POST /sales-orders/{id}/pay` (счёт и категория из
   конфига рецепта).
6. **Отгрузка** (если есть товарные строки): `POST /shipments` без
   serial_codes (FIFO-автовыбор) → `post`. Для `kind=digital` —
   цифровой склад по умолчанию.
7. **Доставка кода** (цифровые строки): `POST /shipments/{id}/deliver`
   (§5.2) — разовая выдача расшифрованных кодов + аудит → рендер письма
   → email (SMTP-коннектор) и/или Telegram-коннектор (канал из рецепта).
8. **Финал**: `flow_runs.status=done`, событие
   `integration.payment.processed`; Telegram-уведомление продавцу по
   `notification_rules` (по желанию).

### 3.3 Участники и роли

- **Покупатель** — вне системы; платит на сайте, получает код.
- **Оркестратор** — служебный api_token (роль user, showcase-chain A).
- **Оператор/админ** (роль `integrations` rw): настраивает connections,
  маппинг, рецепты; видит журнал платежей и флоу; жмёт «повтор».
- **ИИ-агент**: read-only агрегаты (сколько онлайн-продаж, причины
  manual) — без ПДн и кодов; виджет не входит в v1.

### 3.4 Проверка подлинности вебхука (эталон ЮKassa)

ЮKassa **не подписывает** нотификации HMAC — поэтому для неё основной
механизм: получив нотификацию, коннектор делает авторизованный
`GET /v3/payments/{id}` и сверяет статус `succeeded` + сумму. Каркас
`AcquiringConnector` поддерживает оба механизма:

- `verify_webhook(headers, body) -> bool` — HMAC/подпись (Robokassa
  Signature, Т-Касса — свои схемы в их адаптерах);
- `verify_by_fetch(payment_id) -> ConnectorResult` — повторная проверка
  через API (обязателен, если провайдер не подписывает);
- `normalize(payload) -> dict` — единый нормализованный платёж:
  `{payment_id, status, amount, currency, buyer: {email, phone, name},
  lines: [{external_id, sku, name, qty, price}], metadata}`.

Ключ идемпотентности (external_key): у ЮKassa отдельного event_id в
нотификации нет — используем `payment_id + event`; провайдеры с явным
event_id (Stripe-подобные) используют его. UNIQUE(connection_id,
external_key).

## 4. Модель данных (схема integrations, обратимые миграции)

### 4.1 `webhook_endpoints` — расширение (миграция)

`+ connection_id UUID NULL FK connections.id` — привязка приёмника к
провайдеру. Если задан: авторизация вебхука коннектором
(verify_webhook/verify_by_fetch), X-ERP-Token не требуется (ЮKassa не
умеет произвольные заголовки). Если не задан — прежнее поведение
X-ERP-Token (generic-приёмник). `target_module` = `payments`.

### 4.2 `webhook_events` — журнал входящих + идемпотентность

`id, endpoint_id FK, connection_id FK NULL, external_key String(200),
event_type String(100), payload JSONB (сырой), status (new | processed |
duplicate | invalid | error), error Text, received_at, processed_at`.
UNIQUE(connection_id, external_key). Тело — только после успешной
проверки подлинности (invalid — при провале проверки; payload храним и
в этом случае для разбора инцидентов, ответ 401).

### 4.3 `online_payments` — нормализованный платёж

`id, connection_id FK, provider String(50), provider_payment_id
String(100), status (received | processed | manual | ignored), amount
NUMERIC(20,4), currency String(3), buyer JSONB, lines JSONB, metadata
JSONB, webhook_event_id FK, sales_order_id UUID NULL, transaction_id
UUID NULL, shipment_id UUID NULL, created_at, updated_at`.
UNIQUE(connection_id, provider_payment_id). UUID-ссылки на схему учёта —
без FK (границы модулей, паттерн crm_deal_id). `buyer` — ПДн: не
возвращается в списковых API без прав integrations rw, не попадает в
события.

### 4.4 `item_mappings` — сайт-товар → номенклатура

`id, connection_id FK NULL (NULL = глобальный маппинг по sku),
external_item_id String(200), sku String(100) NULL, item_id UUID (без
FK), is_active, created_at`. UNIQUE(connection_id, external_item_id).
Поиск при обработке: точное совпадение external_item_id → sku → нет
совпадения = ошибка `item_not_mapped` (§3.5).

### 4.5 `flow_runs` — исполнение сценария (шаговый журнал)

`id, payment_id FK online_payments, recipe_id FK recipes NULL, status
(running | done | failed | manual), step String (последний завершённый:
counterparty | order | confirm | pay | ship | deliver | notify), context
JSONB (id созданных сущностей, лог шагов), error Text, attempts Int,
started_at, finished_at`. UNIQUE(payment_id) — один прогон на платёж;
ретрай продолжает с `step`, перечитывая `context` (шаги идемпотентны:
«уже есть order_id — пропустить создание»).

### 4.6 Изменений в схеме mgmt_accounting — нет

Все новые эндпоинты учёта (§5.2) работают поверх существующих таблиц
(contacts, counterparties, item_serials).

## 5. API

### 5.1 Интеграции (`/api/v1/integrations`, роли как в модуле)

- `GET /payments?status=&provider=` — журнал платежей (buyer маскируется
  для ro); `GET /payments/{id}` — карточка с историей флоу.
- `POST /payments/{id}/retry` — повторить флоу с последнего шага
  (после правки маппинга/пополнения кодов).
- `POST /webhook-events/{id}/reprocess` — переобработать вебхук
  (нормализация + запуск флоу).
- `GET/POST /item-mappings`, `PATCH /item-mappings/{id}`.
- `GET /connectors` — в каталоге появляются `yookassa`, `tinkoff`
  (каркас), `robokassa` (каркас), `smtp`.
- Recipes: тип action `sales_flow` (definition см. §7). Исполнитель —
  расширение `recipes_executor.py`.

### 5.2 Учёт (`/api/v1/accounting`) — новые сервисные операции

- `POST /counterparties/find-or-create` — тело `{email? , phone?, name?}`;
  ищет contact по email/phone → его counterparty; иначе создаёт пару.
  Возвращает counterparty_id (+`created: bool`). Причина: логика дублей
  и контактов — домен учёта; интеграции не лезут в erp_core.contacts
  напрямую.
- `POST /shipments/{id}/deliver` — разовая выдача расшифрованных кодов
  отгрузки: тело `{channel_note?}`, ответ `{serials: [{item_id, code}]}`
  **один раз**; помечает `delivered_at`, пишет `record_versions`/аудит и
  событие `acc.shipment.delivered` (без кодов в payload). Повторный вызов
  — 409 с фактом доставки (когда/кому). Права: служебный токен или admin.
  Попутно чинится известная полировка: FIFO-выданные коды сохраняются в
  `shipment_lines.serial_codes` при проведении (decisions-registry,
  этап I) — без этого deliver нечего показывать в истории.

### 5.3 Коннекторы (каркас и эталон)

- **`AcquiringConnector(BaseConnector)`** (ABC в connectors/acquiring.py):
  capabilities fetch+webhooks; методы §3.4. Общие поля config:
  `account_id` (счёт зачисления), `default_digital_location`.
- **`YooKassaConnector`** — эталон: Basic-авторизация shopId/secretKey
  (credentials, Fernet), `fetch_payment GET /v3/payments/{id}`,
  normalize (metadata платежа несёт external_item_id строк сайта).
- **`TinkoffPaymentsConnector`, `RobokassaConnector`** — каркас: класс
  зарегистрирован, config_schema описан, методы — `NotImplementedError`
  до спроса пилотов (Robokassa ближе всего: HMAC-подпись уже есть в
  `HttpRestConnector.verify_webhook`).
- **`SmtpConnector`** — push-only: `{to, subject, text}` через smtplib
  (host/port/TLS в config, пароль в credentials). Сетевой импорт —
  только здесь (banned-api линтер дополняется: smtplib разрешён
  исключительно в connectors/).

## 6. События шины (контракты ADR-002, деньги строками)

- `integration.payment.received`: `{payment_id, provider,
  provider_payment_id, amount, currency, lines_count}` — **без ПДн**
  (оркестратор читает детали из БД по payment_id).
- `integration.payment.processed`: `{payment_id, provider_payment_id,
  sales_order_id, transaction_id, shipment_id}`.
- `integration.payment.failed`: `{payment_id, provider_payment_id,
  step, reason}` — причина из фиксированного набора: item_not_mapped,
  price_mismatch, insufficient_stock, period_closed, mapping_error,
  delivery_failed.
- `acc.shipment.delivered`: `{shipment_id, sales_order_id, channel}`.
- `NOTIFY_EVENTS` (notify.py) дополняется: `integration.payment.processed`
  (дефолт-уведомление продавцу «онлайн-продажа состоялась»),
  `integration.payment.failed`, `acc.shipment.delivered`.

## 7. Конструктор: что готово, что новое

Рецепт вебхук-оплаты (definition):

```json
{
  "trigger_event": "integration.payment.received",
  "action": {
    "type": "sales_flow",
    "connection_id": "<yookassa>",
    "config": {
      "account_id": "uuid-счёта",
      "digital_location": "uuid-цифрового-склада",
      "price_tolerance": "0",
      "on_no_items": "transaction_only",
      "on_item_not_mapped": "manual",
      "delivery_channel": "email | telegram | both | none"
    }
  }
}
```

Готовые шаги (существующий код): приём вебхука, рецепты+токены,
создание/confirm/pay/shipment API учёта, Telegram-push, FIFO-выдача,
Fernet-коды. Новые шаги (эта спека): идемпотентный приём+журнал,
нормализация, find-or-create контрагента, маппинг items, шаговый
оркестратор, deliver-выдача кодов, SMTP-коннектор. Многошаговость —
новый тип action в `recipes_executor`; визуальный конструктор — non-goal
(v1 рецепт = JSON/seed-шаблон «Онлайн-продажа цифровых»).

## 8. Сценарии ошибок (каждый — остановка флоу в `manual`, деньги уже учтены или учтены отдельно)

| Ситуация | Поведение | Транзакция денег |
|---|---|---|
| Товар не найден в маппинге | `item_not_mapped`: заказ не создаётся; платёж + контрагент учтены; Telegram-уведомление; оператор заводит маппинг → `retry` | создаётся сразу (без source-заказа) |
| Цена разошлась (> tolerance) | `price_mismatch`: заказ создаётся и подтверждается по ценам платежа, отгрузка/выдача стоп; уведомление | создаётся (по факту платежа) |
| Оплата без товарных строк | `transaction_only` (default): только входящая транзакция + контрагент; заказ не нужен | создаётся |
| Нет кодов на складе (insufficient_stock) | Заказ подтверждён, оплата проведена, отгрузка 422 → `manual`; уведомление «коды кончились»; после пополнения — `retry` | уже проведена |
| Период закрыт (pay 422) | `period_closed` → manual; переоткрытие периода — решение админа | не проведена |
| Доставка письма не удалась | Флоу `done` по учёту (деньги/код проданы), шаг notify — `failed` c ретраями (3×60с), затем manual + уведомление продавцу; повторная отправка — `retry` (шаг notify повторяемый, коды ещё раз читаются из shipment_lines) | — |
| Подпись/статус не подтвердились | `webhook_events.status=invalid`, ответ 401, ничего не создаётся | нет |
| Дубликат вебхука (ретраи ЮKassa) | duplicate, 200, обработка не повторяется | — |

Сторнирование автоматических документов — вручную существующими
операциями (unpost shipment / storno transaction); автосторно при
`payment.canceled` — v2 (рефанды).

## 9. Безопасность

1. **Секреты**: shopId/secretKey, SMTP-пароль — credentials connection
   (Fernet, существующий механизм); webhook `secret_token` — как сейчас,
   показ один раз. В `webhook_events.payload` секретов не бывает
   (проверено форматом нотификаций).
2. **Allowlist (P1, security-plan п.7)**: домены `yookassa.net`,
   `securepay.tinkoff.ru`, `api.telegram.org`, SMTP-хост — в белый
   список исходящих; строгий режим — новые домены запрещены до явного
   разрешения. Журнал исходящих обращений — на каждый вызов коннектора.
3. **Rate limit на `/hooks/{id}`** (security-plan п.8) — nginx + дедуп
   по external_key.
4. **ИИ (ADR-006)**: `webhook_events.payload` и `online_payments.buyer`
   — класс restricted для ИИ: не попадают в промпты/инструменты
   автоматически. Агент может читать агрегаты платежей без ПДн
   (read-only инструмент `online_sales_summary` — вне v1). Анти-injection:
   тела вебхуков в промпты не подставляются вообще.
5. **Коды**: расшифровка только в операции deliver (аудит + разовость);
   в API списков, событиях, логах — только sha256-отпечатки (как сейчас).

## 10. Этапы

- **A. Коннектор ЮKassa + доверенный приём вебхуков**. `AcquiringConnector`
  ABC + `YooKassaConnector` (+каркасы Т-Касса/Robokassa); `connection_id`
  у endpoint; приёмник вызывает verify_webhook/verify_by_fetch (и
  закрывает расхождение докстринга `router.py` с кодом); `webhook_events`
  с идемпотентностью; rate limit nginx; allowlist P1. Приёмка:
  (A1) pytest — нотификация payment.succeeded (fixture) → 202 → строка
  processed, дубль → duplicate без второй обработки; (A2) нотификация с
  чужим/несуществующим payment_id (mock API отвечает canceled) → invalid,
  401, ничего не создано; (A3) `POST /connections` c yookassa → test
  (mock) ok; (A4) ruff banned-api: httpx/smtplib только в connectors/.
- **B. Платёж → документы**. `online_payments`, `item_mappings`,
  `find-or-create` контрагента, action `sales_flow` (шаги counterparty →
  order → confirm → pay), ошибки §8, `retry`/`reprocess`, события
  received/processed/failed. Приёмка: (B1) живой цикл на дев-стенде —
 seed-вебхук «2× код по 500₽» → заказ confirmed + ЗК-номер + транзакция
  ПК- с категорией «Продажи» и source-ссылкой; (B2) повтор того же
  вебхука → изменений нет; (B3) неизвестный товар → manual +
  item_not_mapped, после маппинга retry → заказ создан, второй
  транзакции нет; (B4) платежи без строк → только транзакция;
  (B5) цена −10% при tolerance 0 → price_mismatch, отгрузки нет.
- **C. Автовыдача и доставка цифровых**. FIFO-фикс `shipment_lines.
  serial_codes`; шаги ship → deliver → notify; `SmtpConnector`;
  `acc.shipment.delivered`; доставка email/Telegram по каналу рецепта.
  Приёмка: (C1) полный happy path end-to-end: вебхук → код (старейший
  по sha256-отпечаткам) → sold → письмо ушло (mock SMTP), повторный
  deliver → 409; (C2) кодов нет → manual, пополнение + retry → выдан;
  (C3) SMTP недоступен → учёт done, notify failed с ретраями;
  (C4) код не встречается ни в одном логе/событии (grep по тестам).
- **D. Эксплуатация и полировка**. Журнал/метрики флоу (админ-экран —
  только API в v1), README-раздел, OpenAPI-примеры, seed-рецепт
  «Онлайн-продажа цифровых», дока по настройке ЮKassa (notification URL,
  IP-allowlist провайдера). Приёмка: (D1) docs-чек; (D2) smoke-секция
  «online sale» (mock-провайдер); (D3) ревью против ADR-001/002/003/006
  без замечаний.

Каждый этап — отдельный коммит, миграции обратимые, pytest/ruff/smoke
зелёные.

## 11. Non-goals v1 (осознанно)

Возвраты/рефанды и автосторно по `payment.canceled` (v2 — сторно-цепочка
+ `refund`-коннектор), подписки/рекуррентные платежи (v2), чеки 54-ФЗ
(отдельная задача с ОФД), физические товары с доставкой (только выдача
кода/самовывоз — курьерские интеграции потом), другие провайдеры кроме
ЮKassa (каркасы готовы), визуальный конструктор рецептов (UI), экран
«Онлайн-продажи» в UI (API + существующий журнал интеграций), автосоздание
сделок CRM по онлайн-оплате (настройка v2), вебхуки сайта о заказах без
оплаты (только платёж — триггер).

## 12. Развилки — ЗАКРЫТЫ ревью архитектурного чата 2026-09-10

1. **Цена разошлась** → вариант (а): стоп отгрузки, tolerance в рецепте
   (default 0 — любое расхождение в manual). Защита маржи важнее
   автоматизма.
2. **Контрагент** → find-or-create эндпоинт учёта (граница модулей);
   без email/phone — контрагент «Покупатель сайта» из конфига рецепта.
3. **Оплата до заказа** → осознанный компромисс v1: транзакция без
   source-заказа, сальдо сходится по контрагенту; привязка — v2.
4. **Email** → SMTP-коннектор (self-hosted, дух ADR-001); HTTP-провайдер
   при желании — через существующий http_rest, отдельного коннектора не
   делать.
5. **deliver** → разовая выдача + повторяемый notify. **Правка ревью
   (безопасность): в `shipment_lines` хранить `serial_ids` (ссылки на
   item_serials), НЕ открытые коды** — код остаётся активом в Fernet;
   шаг notify при повторе расшифровывает по serial_ids через
   существующий crypto-модуль. Открытые коды в JSONB строк отгрузки
   запрещены.
6. **webhook_events отдельной таблицей** — да (идемпотентность до
   нормализации; refund/payout в v2).
7. **Т-Касса/Robokassa** — каркас в этапе A, адаптеры по спросу пилотов.
