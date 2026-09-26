# Спека: коннектор Wildberries Seller API (товары/остатки/заказы/комиссии)

Статус: **ревью арх-чата пройдено 2026-09-26; развилки закрыты
(см. §10); к утверждению основателем** · Дата: 2026-09-26 ·
Автор: erp-analyst. ЦА: селлеры маркетплейсов — основные клиенты Квазара.
Паттерн — `ozon-connector-spec.md` (УТВЕРЖДЕНА 2026-09-24); здесь фиксируются
только отличия WB, всё остальное — «как в Ozon».

## 1. Контекст и проблема

Селлер на WB ведёт учёт в Квазаре вручную — та же боль, что у селлера Ozon
(см. ozon-спека §1): маржа неизвестна, остатки на складах WB «в уме», копипаст
заказов и комиссий из личного кабинета.

Целевой сценарий: подключил кабинет (API-Token) → ERP тянет товары/остатки,
заказы и транзакции (комиссии, логистика, хранение, штрафы) → заказы WB —
`sales_orders` в draft, комиссии — расходы по статьям, остатки WB — отчёт;
опционально push наших остатков на WB.

Границы: **однонаправленная синхронизация WB → ERP** (плюс опциональный push
остатков). Сеть — только в коннекторе (ADR-001), работа внутри модуля
`integrations` + публичный API `/api/v1` (служебный токен).

## 2. As-is (чем опираемся)

Всё по ozon-спека §2 (Connector SDK, `sync_jobs`/`sync_runs`, `item_mappings`,
`sales_orders`, allowlist egress `connectors/egress.py`), плюс:

- **Эталон-паттерн маркетплейс-коннектора** — `OzonSellerConnector`
  (`connectors/ozon.py`, спека `docs/design/ozon-connector-spec.md`):
  тот же каркас (test_connection/fetch_*/push_stocks, normalize_*,
  куратор-воркер, документы через публичный API).
- Коннектора WB в системе **нет** — второй маркетплейс-коннектор.

## 3. To-be

### 3.1 Отличия WB от Ozon (суть спеки)

1. **Аутентификация**: один `API-Token`, заголовок `Authorization: <token>`
   (НЕ `Bearer`). Credentials (Fernet): `api_token`.
2. **Идентификатор заказа**: у WB нет posting_number — свой UID заказа;
   идемпотентность по UNIQUE(connection_id + uid).
3. **Склады**: у WB несколько `warehouseID` — нужна привязка к нашим
   локациям для push остатков (см. §9.1).
4. **Структура комиссий**: комиссия маркетплейса, логистика, хранение,
   штрафы, налог — отдельные статьи расходов «WB: …» (seed-справочник).
5. **Выплаты**: еженедельные — в v1 НЕ проводим (то же решение, что Ozon,
   ozon-спека §10.2).
6. **Rate limit**: 100 запросов/мин (строже Ozon) — throttling в коннекторе,
   429 → backoff как в ozon-критерии 8.

Base URL: `https://seller-api.wildberries.ru` (переопределяем в config для
моков). Allowlist egress: домен `seller-api.wildberries.ru`.

### 3.2 Компоненты

**`WBSellerConnector(BaseConnector)`** (`connectors/wb.py`):

- code = `wb_seller`, capabilities fetch+push (без webhooks, только poll).
- config_schema: `base_url`, `timeout_seconds`, `warehouse_ids`
  (привязка WB warehouseID → наша локация, для push), `auto_create_orders:
  bool` (default off), `price_tolerance`.
- Методы:
  - `test_connection()` — GET `/content/v2/get/cards/list` с limit 1;
  - `fetch_products()` — GET `/content/v2/get/cards/list` (nmID, артикул,
    цена, остатки);
  - `fetch_stocks()` — GET `/api/v3/stocks` (остатки по складам WB);
  - `fetch_orders(since)` — GET `/api/v3/orders` (UID, nmID, размер, цена,
    дата, статус; инкремент по `since`);
  - `fetch_transactions(since)` — GET `/finance/v1/transactions` (комиссии,
    логистика, хранение, штрафы, налог, выплаты);
  - `push_stocks(items, warehouse_id)` — PUT `/api/v3/stocks/{warehouseId}`
    (sku из маппинга → наш остаток по привязанной локации);
  - `normalize_*` — приведение к единым словарям (паттерн Ozon).

**Куратор синкранизации** — воркер `wb_sync_task` (расширение `tasks.py`),
по паттерну `ozon_sync_task`.

### 3.3 Потоки данных (как в Ozon, отличия)

1. **Товары/цены** (раз в час) → `wb_products` (кэш-каталог; nmID +
   артикул → маппинг `item_mappings`, external_item_id = артикул/nmID —
   см. §9.2). Автосоздания номенклатуры в v1 нет.
2. **Остатки WB** (раз в час) → снапшот `wb_stocks` (чужой склад: WITHOUT
   movements, перезапись за прогон, история по `fetched_at`).
3. **Заказы** (каждые 15 мин) → `wb_orders` (UNIQUE connection+uid).
   `auto_create_orders=on` и все позиции смаплены → контрагент «WB»,
   `POST /accounting/sales-orders` → draft, без confirm. Отмена на WB →
   `wb_orders.status=cancelled`; черновик удалить, подтверждённый — вручную.
4. **Транзакции** (раз в час) → `wb_transactions` (UNIQUE
   connection+operation_id). Каждая операция → отдельный расход по статье
   («WB: Комиссия», «WB: Логистика», «WB: Хранение», «WB: Штрафы»,
   «WB: Налог»), контрагент «WB» (решение Ozon §10.1 — одна транзакция на
   операцию). Выплаты в v1 не проводим.
5. **Push остатков** (on-demand + cron) — наш остаток по привязанной
   локации → PUT `/api/v3/stocks/{warehouseId}` для смапленных items.

### 3.4 Роли

Как в ozon-спека §3.4 (оператор/админ — connection и маппинг; бухгалтер —
черновики и расходы; ИИ — вне v1).

## 4. Модель данных (схема integrations, обратимые миграции)

- `wb_products`: `id, company_id, connection_id FK, nm_id String(50),
  vendor_code String(200) NULL, name, price NUMERIC(20,4), currency,
  fetched_at`. UNIQUE(connection_id, nm_id).
- `wb_stocks`: `id, connection_id FK, nm_id, warehouse_id String(100),
  qty Int, fetched_at`. Снапшот (перезапись за прогон).
- `wb_orders`: `id, connection_id FK, uid String(100),
  status (new|delivered|cancelled), order_date, amount NUMERIC(20,4),
  currency, lines JSONB (nmID, размер, цена), sales_order_id UUID NULL,
  mapping_error bool default false, created_at, updated_at`.
  UNIQUE(connection_id, uid).
- `wb_transactions`: `id, connection_id FK, operation_id String(100),
  operation_type String(50) (commission|logistics|storage|penalty|tax|
  payment|…), amount NUMERIC(20,4), items JSONB, posted_at,
  transaction_id UUID NULL, created_at`. UNIQUE(connection_id,
  operation_id).
- `item_mappings`, `sync_jobs`, `sync_runs` — без изменений.
- mgmt_accounting — без изменений схемы; статьи «WB: …» — seed-справочник.

## 5. API

`/api/v1/integrations` (зеркально Ozon):
- `GET/POST /wb/products?connection_id=`, `GET /wb/stocks`, `GET /wb/orders`,
  `GET /wb/transactions`.
- `POST /wb/sync` — `{connection_id, kinds: [...]}`.
- `POST /wb/push-stocks` — `{connection_id, warehouse_id?, item_ids?}`.
- `GET /wb/margin?from=&to=&include_cost=` — выручка (delivered) −
  комиссии/логистика/хранение/штрафы − себестоимость (флаг, как Ozon §10.3).
- Маппинг — существующие `item_mappings` CRUD.

`GET /connectors`: в каталоге `wb_seller`.

## 6. События шины (ADR-002)

- `integration.wb.orders.synced`: `{connection_id, new_orders,
  cancelled_orders}` — без ПДн покупателей.
- `integration.wb.transactions.synced`: `{connection_id, new_count,
  total_amount}`.
- NOTIFY_EVENTS: `integration.wb.orders.synced` (по желанию).

## 7. Критерии приёмки (Given/When/Then)

1. G подключён wb-коннектор с валидным API-Token; W `POST
  /connections/{id}/test`; T ok=True (мок-сервер), заголовок Authorization
  без Bearer.
2. G прогон sync products; W карточки получены; T `wb_products` заполнены
  (nm_id, vendor_code, price), повторный прогон без дублей.
3. G новый заказ WB (2 позиции, обе смаплены, auto_create_orders=on); W sync;
  T создан `sales_orders` draft с контрагентом «WB», строки по маппингу,
  цены Decimal-строками из заказа.
4. G заказ с несмапленной позицией; W sync; T `wb_orders` записан,
  sales_order НЕ создан, `mapping_error=true`, уведомление; после маппинга
  и ретрая — заказ создан.
5. G заказ отменён на WB; W sync; T `wb_orders.status=cancelled`; черновик
  удалён, подтверждённый не тронут.
6. G транзакции (комиссия+логистика+хранение+штраф); W sync; T созданы
  расходы по статьям «WB: …», повторный sync без дублей (UNIQUE
  operation_id).
7. G транзакция типа выплата (payment); W sync; T записана в
  `wb_transactions`, расход/движение денег НЕ создано.
8. G push-stocks по warehouse_id с привязанной локацией; W наш остаток 10
  шт; T в мок WB ушёл PUT /api/v3/stocks/{warehouseId} с qty=10.
9. G WB отвечает 429 (rate limit 100/мин); W sync; T throttling в
  коннекторе, sync_run error c backoff (ретрай через 5 мин), данные не
  потеряны.
10. G API-Token отозван (401/403); W sync; T sync_run error `auth_failed`,
  connection degraded, уведомление админу.
11. G период закрыт; W создание расхода; T транзакция не проведена, задание
  в manual-очереди.
12. G отчёт margin за месяц; T выручка − комиссии − себестоимость = прибыль,
  суммы бьются с `wb_orders` и `wb_transactions`.
13. Ruff banned-api: httpx только в connectors/; ruff/pytest/smoke зелёные.

## 8. Что НЕ входит в v1

FBO/FBS-специфика отправок и этикетки; чаты с покупателями; реклама;
автосоздание номенклатуры по каталогу WB; проводка еженедельных выплат как
движений денег (bank); вебхуки WB (только poll); выгрузка цен и карточек
WB ← ERP (только остатки); UI-виджеты (только API и существующие экраны
интеграций); прочие маркетплейсы.

## 9. Открытые вопросы (с рекомендациями)

1. **Привязка складов WB**: config `warehouse_ids` (WB warehouseID → наша
   локация) достаточно для v1, или нужна отдельная таблица
   `wb_warehouses` (история, UI-редактирование)? Рекомендую: v1 — config
   (как Ozon), таблицу — при запросе от операторов.
2. **Ключ маппинга товаров**: артикул (vendor_code, задаёт селлер) или
   nmID (стабильный, но внутренний)? Рекомендую: external_item_id =
   vendor_code (человекочитаемо, совпадает с нашим SKU), nmID храним в
   `wb_products`.
3. **Налог в транзакциях WB**: отдельная статья «WB: Налог» или игнорируем
   (учёт налогов — зона бухгалтера)? Рекомендую: отдельная статья,
   проведение расхода — как у остальных.
4. **auto_create_orders default** — off (как Ozon §10.4), подтвердить.

## 10. Решения ревью архитектурного чата (2026-09-26)

1. **Привязка складов** → config в v1 (warehouseID → наш location в
   connection.config.warehouses; таблица — при 3+ складах WB).
2. **Ключ маппинга** → vendor_code (человекочитаемый; nmID в external_id
   для дубль-проверки).
3. **Налог WB** → отдельная статья «Налог WB» (не игнорируем — реальные
   деньги селлера).
4. **auto_create_orders** → default off (подтверждено, как Ozon).
5. **Неизвестные operation_type** → fallback-статья «WB: Прочее»
   (рекомендация аналитика принята).
