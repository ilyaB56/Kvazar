# Спека: коннектор Ozon Seller API (товары/остатки/заказы/комиссии)

Статус: **УТВЕРЖДЕНА основателем 2026-09-24; ревью арх-чата пройдено;
развилки §9 закрыты (§10)** · Дата: 2026-09-24 ·
Автор: erp-analyst. ЦА: селлеры маркетплейсов — основные клиенты Квазара.

## 1. Контекст и проблема

Селлер на Ozon ведёт учёт в Квазаре вручную: копирует заказы, комиссии и
выплаты из личного кабинета, остатки на складах Ozon не видит, цены в ERP
расходятся с витриной. Итог: маржа по маркетплейсу неизвестна, остатки
«в уме», часы копипаста.

Целевой сценарий: подключил кабинет (Client-Id + Api-Key) → ERP сама
каждый час тянет товары/цены и каждые 15 минут заказы и транзакции
(комиссии, логистика) → заказы Ozon становятся `sales_orders`, комиссии —
расходами по статьям, остатки Ozon видны отчётом; опционально — push наших
остатков на Ozon.

Границы: **однонаправленная синхронизация Ozon → ERP** (плюс опциональный
push остатков). Сеть — только в коннекторе (ADR-001), вся работа с Ozon —
внутри модуля `integrations` + публичный API `/api/v1` для создания
документов учёта (паттерн sales-automation: оркестратор ходит в учёт
служебным токеном).

## 2. As-is (чем опираемся)

- **Connector SDK**: `BaseConnector` (test_connection/fetch/push),
  реестр, `Connection` с Fernet-секретами — `src/modules/integrations/sdk.py`,
  `crypto.py`. Эталон API-коннектора — `YooKassaConnector`
  (`connectors/acquiring.py`: `_headers`, test_connection, guarded_get,
  обработка `EgressBlocked`/`httpx.HTTPError`).
- **Sync jobs**: `sync_jobs` (cron, endpoint, direction, emit_event) +
  `sync_runs` + Celery-планировщик `run_due_sync_jobs_task` —
  `src/modules/integrations/models.py:99–132`, `tasks.py:42–65`.
- **Вебхуки/платежи/маппинг** (этапы A–B sales-automation):
  `webhook_events` с идемпотентностью, `online_payments`,
  `item_mappings` (external_item_id → item_id/sku) — переиспользуем
  `item_mappings` для товаров Ozon (external_item_id = offer_id/SKU).
- **Продажи**: `sales_orders` (draft→confirmed→…→shipped, ЗК-номер при
  confirm, курс заморожен — ADR-003), `POST /sales-orders/{id}/pay` —
  `src/modules/mgmt_accounting/features/sales/`.
- **Allowlist egress**: `connectors/egress.py` (guarded_get,
  EgressBlocked) — домен `api-seller.ozon.ru` добавляем в белый список.
- Аналогичного коннектора маркетплейса в системе **нет** — это первый.

## 3. To-be

### 3.1 Принципы

1. Сеть — только в `connectors/` (ADR-001); все запросы — через
   `guarded_get`/`guarded_post` с allowlist `api-seller.ozon.ru`.
2. Ozon — **чужой склад**: остатки Ozon НЕ создают movements в ERP,
   только отчёт `ozon_stocks` (снапшот). Movements — только по нашему
   складу при отгрузке/продаже (решает оператор).
3. Деньги — Decimal-строки (ADR-003); суммы комиссий — NUMERIC.
4. Идемпотентность: заказ Ozon создаётся один раз
   (UNIQUE connection+posting_number); транзакции Ozon — один раз
   (UNIQUE connection+operation_id).
5. Автосоздание заказов — **настраиваемое** (off/on per connection):
   заказ создаётся в статусе draft, оператор подтверждает сам (Ozon —
   не «наш» клиент, контрагент один: «Ozon», а не конечный покупатель).
6. Комиссии Ozon — расходы по статьям («Комиссия Ozon», «Логистика
   Ozon», «Реклама Ozon» — справочник, seeded), контрагент «Ozon».

### 3.2 Компоненты

**`OzonSellerConnector(BaseConnector)`** (`connectors/ozon.py`):

- code = `ozon_seller`, capabilities fetch+push (без webhooks — Ozon
  нотификации о заказах в v1 не используем, только poll).
- Credentials (Fernet): `client_id`, `api_key`. Заголовки:
  `Client-Id` + `Api-Key` (НЕ Bearer). Base URL:
  `https://api-seller.ozon.ru` (переопределяем в config для моков).
- config_schema: `base_url`, `timeout_seconds`, `warehouse_ids`
  (список складов для push остатков), `auto_create_orders: bool`,
  `price_tolerance`.
- Методы:
  - `test_connection()` — GET `/v4/product/info/stocks` с limit 1;
  - `fetch_products()` — GET `/v3/product/list` (+ `/v4/product/info/prices`);
  - `fetch_stocks()` — GET `/v4/product/info/stocks`;
  - `fetch_orders(since)` — GET `/v5/order/list` (инкремент по `since`,
    статус-фильтр delivered/cancelled);
  - `fetch_transactions(since)` — GET `/v1/report/transactions`;
  - `push_stocks(items)` — POST `/v1/product/import/stocks`
    (item_id+sku из маппинга → наш остаток по складу);
  - `normalize_*` — приведение к единым словарям (паттерн
    `AcquiringConnector.normalize`).

**Куратор синхронизации** (воркер `ozon_sync_task`, расширение
`tasks.py`): читает `sync_jobs` типа ozon, вызывает коннектор,
пишет результаты, создаёт документы через публичный API (служебный
api_token, паттерн sales-automation).

### 3.3 Потоки данных

1. **Товары/цены** (раз в час): `/v3/product/list` + `/v4/.../prices` →
   `ozon_products` (кэш-каталог: sku, name, price; upsert по
   connection+sku). Маппинг на `items` — вручную через `item_mappings`
   (external_item_id = offer_id). Автосоздания номенклатуры в v1 нет.
2. **Остатки Ozon** (раз в час, вместе с товарами): `/v4/.../stocks` →
   снапшот `ozon_stocks` (перезапись, история — `fetched_at`).
   Доступны API-отчётом; UI-виджет — v2.
3. **Заказы** (каждые 15 мин): `/v5/order/list?since=last_sync` →
   `ozon_orders` (UNIQUE connection+posting_number). Если
   `auto_create_orders` и все строки смаплены: контрагент «Ozon»,
   `POST /accounting/sales-orders` → draft (цены из заказа Ozon, без
   confirm — оператор проверяет). Отмена на Ozon (статус cancelled)
   → пометка `ozon_orders.status=cancelled`; сторно черновика = удалить
   черновик; сторно подтверждённого — вручную (существующие операции).
   POST `/v5/order/cancel` из ERP — **не вызываем в v1** (только чтение).
4. **Транзакции** (раз в час): `/v1/report/transactions` →
   `ozon_transactions` (UNIQUE connection+operation_id). Каждый тип
   (комиссия, логистика, последняя миля, реклама, возврат) маппится на
   статью расходов; по новым транзакциям создаётся **одна сводная
   транзакция-расход** за прогон sync (или по одной на операцию —
   открытый вопрос §9.1) через API учёта, контрагент «Ozon».
   Выплаты (transfer) в v1 не проводим как деньги (см. §9.2).
5. **Push остатков** (on-demand + cron по желанию): наш склад →
   `POST /v1/product/import/stocks` для смапленных items.

### 3.4 Роли

- Оператор/админ (`integrations` rw): создаёт connection, маппит товары,
   включает auto_create_orders, запускает/ретраит sync.
- Бухгалтер (`accounting` rw): подтверждает черновики заказов, работает
  с расходами.
- ИИ (ADR-006): read-only агрегаты (маржа по Ozon) — вне v1.

## 4. Модель данных (схема integrations, обратимые миграции)

- `ozon_products`: `id, company_id, connection_id FK, offer_id String(200),
  sku String(100) NULL, name, price NUMERIC(20,4), currency,
  fetched_at`. UNIQUE(connection_id, offer_id).
- `ozon_stocks`: `id, connection_id FK, offer_id, warehouse_id String(100),
  qty Int, fetched_at`. Снапшот (перезапись за прогон).
- `ozon_orders`: `id, connection_id FK, posting_number String(100),
  status (new|delivered|cancelled), order_date, amount NUMERIC(20,4),
  currency, lines JSONB, sales_order_id UUID NULL (без FK),
  created_at, updated_at`. UNIQUE(connection_id, posting_number).
- `ozon_transactions`: `id, connection_id FK, operation_id String(100),
  operation_type String(50), amount NUMERIC(20,4), items JSONB(детали),
  posted_at (дата операции Ozon), transaction_id UUID NULL (ссылка на
  созданный расход), created_at`. UNIQUE(connection_id, operation_id).
- `item_mappings`, `sync_jobs`, `sync_runs` — без изменений
  (переиспользуются).
- mgmt_accounting — без изменений схемы; статьи расходов «Ozon: …» —
  seed-справочник.

## 5. API

`/api/v1/integrations`:
- `GET/POST /ozon/products?connection_id=`, `GET /ozon/stocks` (отчёт),
  `GET /ozon/orders`, `GET /ozon/transactions`.
- `POST /ozon/sync` — внеочередной прогон (тело: `{connection_id,
  kinds: [products|stocks|orders|transactions]}`).
- `POST /ozon/push-stocks` — push остатков (тело: `{connection_id,
  item_ids?}` — по умолчанию все смапленные).
- `GET /ozon/margin?from=&to=` — сводка «Ozon: комиссия и прибыль»:
  выручка (delivered orders) − комиссии/логистика/реклама (ozon_transactions)
  − себестоимость (по items, при маппинге) за период.
- Маппинг — существующие `item_mappings` CRUD.

`GET /connectors`: в каталоге `ozon_seller`.

## 6. События шины (ADR-002)

- `integration.ozon.orders.synced`: `{connection_id, new_orders,
  cancelled_orders}` — без ПДн покупателей.
- `integration.ozon.transactions.synced`: `{connection_id, new_count,
  total_amount}`.
- NOTIFY_EVENTS: `integration.ozon.orders.synced` (по желанию).

## 7. Критерии приёмки (Given/When/Then)

1. G подключён ozon-коннектор с валидными ключами; W `POST
  /connections/{id}/test`; T ok=True (мок-сервер).
2. G прогон sync products; W товары получены; T `ozon_products`
  заполнены, повторный прогон не создаёт дублей.
3. G новый заказ Ozon (2 позиции, обе смаплены, auto_create_orders=on);
  W прогон sync orders; T создан `sales_orders` в draft с контрагентом
  «Ozon», строки по маппингу, цены Decimal-строками из заказа.
4. G заказ с несмапленной позицией; W sync; T `ozon_orders` записан,
  sales_order НЕ создан, флаг `mapping_error`, уведомление оператору;
  после маппинга и ретрая — заказ создан.
5. G заказ отменён на Ozon; W sync; T `ozon_orders.status=cancelled`;
  черновик-заказ удалён, подтверждённый — не тронут.
6. G новые транзакции (комиссия+логистика); W sync; T создан расход
  по статьям «Комиссия Ozon»/«Логистика Ozon», повторный sync — без
  дублей (UNIQUE operation_id).
7. G push-stocks; W наш остаток 10 шт по смапленному item; T в мок
  Ozon ушёл POST /v1/product/import/stocks с qty=10.
8. G Ozon отвечает 429; W sync; T sync_run status=error c backoff
  (ретрай через 5 мин), данные не потеряны.
9. G Api-Key отозван (403/401); W sync; T sync_run error
  `auth_failed`, connection помечен degraded, уведомление админу.
10. G период закрыт; W создание расхода; T транзакция не проведена,
  задание в manual-очередь, оператор решает (переоткрытие — админ).
11. G отчёт margin за месяц; W есть заказы и комиссии; T выручка −
  комиссии − себестоимость = прибыль, суммы бьются с ozon_orders и
  ozon_transactions.
12. Ruff banned-api: httpx только в connectors/; ruff/pytest/smoke зелёные.

## 8. Что НЕ входит в v1

FBO/FBS-логика отправок и этикетки; чаты с покупателями; реклама
(кроме чтения расходов); отмена заказов из ERP (POST /v5/order/cancel);
автосоздание номенклатуры по каталогу Ozon; проводка выплат как
движений денег (bank); вебхуки Ozon (только poll); выгрузка цен и
карточек товаров Ozon ← ERP (только остатки); другие маркетплейсы
(WB — отдельная спека, каркас-паттерн тот же); UI-виджеты (только API
и существующие экраны интеграций).

## 9. Открытые вопросы (с рекомендациями)

1. **Гранулярность расходов**: одна транзакция на операцию Ozon (прозрачнее,
   но шумно в реестре) vs сводная за прогон (чище, сложнее сторно).
   Рекомендую: одна на операцию — сторно/сверка с отчётом Ozon тривиальны.
2. **Выплаты Ozon (transfer)**: в v1 игнорируем в денежных потоках или
   создаём транзит «выплата получена»? Рекомендую: не проводим (банк-фид
  / ручная выписка закроет); пересмотреть в v2 вместе с bank-коннектором.
3. **Себестоимость в отчёте маржи**: брать из items (last cost) или
   только «выручка − комиссии Ozon»? Рекомендую: оба режима, флаг в API.
4. **auto_create_orders default**: off (безопасно) или on? Рекомендую off
   для v1, включается per-connection.
5. **Заказ по контрагенту «Ozon» vs конечный покупатель**: Ozon не даёт
   ПДн покупателя в API для FBO — контрагент «Ozon» единственно возможен;
   подтвердить у арх-чата.

## 10. Решения ревью архитектурного чата (2026-09-24)

1. **Гранулярность расходов** → одна транзакция на операцию Ozon
   (прозрачное сторно, тривиальная сверка с отчётом Ozon).
2. **Выплаты (transfer)** → в v1 НЕ проводим (банк-фид/ручная выписка
   закроет; v2 вместе с bank-коннектором).
3. **Себестоимость в марже** → оба режима, флаг `include_cost` в API.
4. **auto_create_orders** → default off, включается per-connection.
5. **Контрагент** → «Ozon» (API не даёт ПДн конечного покупателя для
   FBO; подтверждено арх-чатом).
