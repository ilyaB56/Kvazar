# Спека: инструменты аналитика и разработчика — браузер таблиц, ракурсы ведения, отладчик

Статус: **ПРОЕКТ** (написана erp-analyst по утверждённым владельцем направлениям
отзыва №2 п.2; ревью архитектурного чата не пройдено) · Дата: 2026-10-04 ·
Чат: архитектурный (задание — «интерфейсы аналитика и разработчика», решение
арх-чата 2026-09-10 + утверждение владельцем четырёх направлений 2026-10)

## 1. Контекст и проблема

Обратная связь от пилотов и владельца: системе не хватает инструментов
«взгляда под капот». Сегодня, чтобы ответить на вопросы «что лежит в таблице
X», «кто и когда менял этот справочник», «почему упала синхронизация», нужно
подключаться к БД напрямую (psql/DBeaver) — это доступ только у разработчика,
без аудита, без орг-изоляции и с риском случайно выполнить UPDATE на проде.

Владелец утвердил четыре направления (обязательные решения, не меняются):

1. **Браузер таблиц** (аналог SE16N) — read-only просмотр любых таблиц с
   фильтрами, сортировкой, пагинацией и экспортом CSV.
2. **Ракурсы ведения** (аналог SM30) — пользовательские редактируемые
   представления поверх таблиц; сам факт возможности СОЗДАНИЯ ракурсов
   обязателен; правка — только через API-слой с валидацией и аудитом.
3. **Отладчик** — наблюдаемость без исполнения кода/SQL на проде: единая
   консоль журналов, трассировка документа, диагностика.
4. **Привилегии поверх ролей**: `table_browser`, `maint_views`, `devtools` —
   выдаются существующей механикой делегирования (`user_permissions`).

Граница безопасности задана решением арх-чата 2026-09-10: SQL-консоль —
только read-only и только при дев-флаге (в эту спеку НЕ входит, отдельное
решение); редактирование транзакционных таблиц (двойная запись, сторно,
версии) запрещено — целостность важнее удобства.

Целевые пользователи: аналитик организации (проверка данных, расследование
расхождений), разработчик/интегратор (отладка коннекторов и событий),
платформенный админ (глобальные справочники, шаблоны ракурсов, журналы
всей установки).

## 2. Термины

- **Браузер таблиц** — read-only просмотр любой таблицы БД через UI/API:
  метаданные, фильтры, сортировка, пагинация, экспорт CSV. Без JOIN.
- **Ракурс ведения** (maintenance view) — именованная конфигурация «таблица +
  колонки + фильтр области + права», через которую можно править данные
  ограниченного круга таблиц. Не SQL-представление в БД — конфигурация в
  JSONB, исполняется API-слоем.
- **Режим domain** — ракурс, маппящийся на существующий доменный API модуля
  (для сущностей с бизнес-логикой: валидации, события, номерные серии).
- **Режим direct** — генерический CRUD по таблице из белого списка (в коде),
  только для таблиц без бизнес-инвариантов.
- **Фиксированный фильтр области** — неизменяемая часть WHERE ракурса,
  заданная в его определении (например, `is_transit = false`).
- **Платформенный шаблон ракурса** — ракурс с `company_id = NULL`; виден всем
  организациям, правится только платформенным контекстом.
- **Маскируемая колонка** — колонка с секретом (хэш/шифртекст токена или
  пароля); в браузере и экспортах всегда выводится как `***`.
- **RO-подключение** — отдельное подключение к PostgreSQL под ролью без прав
  на запись, через которое идут все запросы браузера таблиц.
- **Автокомпания** — неявный фильтр `company_id = <org из JWT>` на таблицах
  с `company_id`, применяемый браузером и ракурсами в контексте организации.
- Используются существующие термины проекта: организация/тенант,
  платформенный админ (`is_platform_admin`), проведение/сторно, двойная
  запись, `doc_sequences`, `events_log`, `record_versions`, egress-журнал,
  `sync_runs`, `flow_runs`, outbox, привилегия/грант.

## 3. As-is (ссылки на код)

Схемы и таблицы (единый реестр моделей `Base.metadata`, `src/db.py:9-15`;
подсчёт на 2026-10-04): `erp_core` — 20 таблиц (`src/core/models.py`),
`mgmt_accounting` — 23 (справочники `models.py:25-143` + фичи
`features/{inventory,purchasing,sales,production}/models.py`),
`integrations` — 19 (`src/modules/integrations/models.py`),
`mini_crm` — 4 (`src/modules/mini_crm/models.py`),
`ai_agent` — 6 (`src/modules/ai_agent/models.py`). Итого 72 ORM-таблицы.
Платформенные глобальные таблицы без `company_id`: `rates`, `doc_types`,
`units` (`src/modules/mgmt_accounting/models.py:80-124`),
`connections` с `company_id NULL` = платформенные
(`src/modules/integrations/models.py:22-27`).

- **Инструментов просмотра таблиц нет вообще.** Прямой доступ к данным —
  только через БД вне системы.
- **Журналы частично**: `GET /events/log` — admin-only, фильтр только по
  `action`, `limit` без пагинации, максимум 500 строк
  (`src/core/router.py:2088-2106`); `GET /events/outbox` — аналогично
  (`router.py:2069-2085`); `sync_runs`/`flow_runs`/`webhook_events` —
  отдельными экранами модуля интеграций без единой консоли. Egress-журнал —
  записи `action='egress.request'` в том же `events_log`
  (`src/modules/integrations/connectors/egress.py:100-127`).
- **История версий** — модульно: `GET /accounting/history/{entity_type}/{id}`
  (`src/modules/mgmt_accounting/router.py:605-620`), `/crm/history/...`
  (`src/modules/mini_crm/router.py:285`). `entity_type` — строковые ключи
  модулей: `acc.transaction`, `acc.sales.order`, `crm.deal`, `user` и т.п.
  (вызовы `record_version(...)` в сервисных слоях). Общей трассировки
  «документ → версии + события» нет.
- **Диагностики нет**: `/health` — одна строка (`src/main.py:38-40`),
  `/api/v1/modules` — реестр модулей (`main.py:43-51`), `GET /system/version`
  (`router.py:2148-2159`). Тест коннектора есть только в интеграциях
  (`POST /integrations/connections/{id}/test`).
- **Права**: `MODULES = ("accounting", "crm", "integrations", "ai", "system")`
  (`src/core/auth.py:247`); личные гранты `user_permissions` + эффективный
  уровень `max(роль, личная)` (`auth.py:269-285`); зависимость
  `require_module(module, level)` (`auth.py:288-312`); делегирование и каскад
  реализованы (`router.py:461-620`). Ключи инструментов в матрице отсутствуют.
- **Орг-изоляция**: `CompanyScoped` — org из JWT (`auth.py:228-242`);
  платформенный контекст — клейм `pl` (`auth.py:217-225`).
- **Пагинация**: `PageParams`/`Page`, конверт `{"items": [...], "total": N}`
  (`src/core/pagination.py`); фронт — `PaginatedList` (порция 50, infinite
  scroll, «Загрузить все») — `frontend/src/components/ui/PaginatedList.vue`.
- **Аудит и версии**: `AuditEvent` → `events_log`
  (`src/core/models.py:114-129`); `RecordVersion` → `record_versions` +
  хелпер `record_version(db, entity_type, entity_id, changed_by, diff,
  reason)` (`src/core/versioning.py:17-36`).
- **Подключение к БД одно**, на `settings.database_url`
  (`src/db.py:14`, `src/config.py:10`, `docker-compose.yml:37`) —
  read-only роли нет.
- **Дыра для ракурсов domain-режима**: у контрагентов нет PATCH — только
  GET/POST/find-or-create (`src/modules/mgmt_accounting/router.py:277-347`).
  У сделок CRM PATCH есть (`src/modules/mini_crm/router.py:240`).
- **UI**: секция навигации «Система» (Интеграции + Настройки,
  `frontend/src/layouts/ErpShell.vue:84-89`); свой UI-кит (DataTable,
  Dialog, Tabs и др.), Element Plus удалён (реестр, этап G).

## 4. To-be: обзор и роли

Три инструмента, один раздел UI «Система → Инструменты», одна точка входа
по правам. Общие инварианты всего блока:

- **Чтение — всем обладателям соответствующей привилегии; запись — только
  через API-слой** (валидация + `record_versions` + `events_log`), никогда
  прямым UPDATE мимо доменной логики (кроме direct-режима на таблицах белого
  списка, где бизнес-инвариантов нет by design).
- **Автокомпания**: в контексте организации запросы к таблицам с
  `company_id` неявно фильтруются; попытка передать фильтр по `company_id`
  вручную — 422. В платформенном контексте (клейм `pl`, без org) фильтр не
  применяется — это осознанное супер-право платформы, каждый такой просмотр
  журналируется с пометкой `platform: true`.
- **Только параметризованные запросы**; идентификаторы (схема, таблица,
  колонка) не вставляются в SQL строкой — сверяются с кэшем метаданных,
  несовпадение → 422.
- **Отдельное RO-подключение к БД** для всех read-запросов браузера
  (§9.2). Мутации ракурсов идут через основную сессию ORM (это доменные
  операции API-слоя, не «сырые» запросы).
- **Никакого исполнения кода/SQL на проде**: отладчик — только чтение
  журналов и метрик.

**Аналитик организации** (привилегии `table_browser` [+ `maint_views`]):
смотрит любые таблицы своей организации, выгружает CSV, ведёт справочники
через ракурсы, трассирует документ.

**Разработчик/интегратор** (`devtools`): читает единую консоль журналов
(аудит/egress/синхронизации/флоу), трассирует сущность по `entity_id`,
смотрит диагностику (версии, здоровье, outbox).

**Платформенный админ**: всё вышеперечисленное в платформенном контексте
(все организации + платформенные таблицы: `connections` с NULL, `rates`,
`doc_types`, `units`, `companies`, `signup_requests`), создаёт платформенные
шаблоны ракурсов.

**Админ организации**: как аналитик, плюс выдаёт/делегирует привилегии
инструментов существующей механикой (`PUT /users/{id}/permissions`).

## 5. Привилегии инструментария

Три новых ключа в универсуме модулей прав:

```
TOOL_MODULES = ("table_browser", "maint_views", "devtools")
MODULES = ("accounting", "crm", "integrations", "ai", "system") + TOOL_MODULES
```

- Константа `MODULES` расширяется (`src/core/auth.py:247`) — всё остальное
  наследуется бесплатно: `/me/permissions` отдаёт эффективные уровни и
  личные гранты (`router.py:1955-1970`), матрица ролей строится по `MODULES`
  (`_role_permissions`, `router.py:1947-1952`), делегирование выдаёт rw-модули,
  каскад при потере прав работает по тем же строкам `user_permissions`.
- **Встроенные роли ничего не получают по умолчанию** — у `user`/`readonly`
  нет строк `role_permissions` на новые ключи → `none`. `admin` — `rw`
  по ветке кода. Доступ появляется только личным грантом (или новой строкой
  в кастомной роли через матрицу).
- Семантика уровней (единая конвенция «rw = мутации»):

| ключ | ro | rw |
|---|---|---|
| `table_browser` | просмотр таблиц, метаданные, CSV | = ro (мутаций у инструмента нет) |
| `maint_views` | список ракурсов, чтение строк | правка данных через ракурс, создание/изменение ракурсов своей организации |
| `devtools` | журналы, трассировка, диагностика | = ro |

- Право «использования конкретного ракурса» — поле определения ракурса
  (§7.1, `access`), поверх привилегии: привилегия — необходимое условие,
  `access` — уточнение круга.
- API-токены (`ApiPrincipal`): личных грантов у токена нет, ходит по роли
  токена (`auth.py:298-299`) — инструменты по X-API-Token недоступны, если
  роль токена не даёт матричных прав. Осознанная граница v1 (как в
  role-delegation §5).

## 6. Этап A — браузер таблиц

### 6.1 Метаданные таблиц

Кэш метаданных строится при старте приложения из `Base.metadata` (единый
реестр всех моделей, `src/db.py:9`) и обновляется по требованию эндпоинтом
перестройки (платформенный контекст; на случай горячего добавления модуля).

Схема элемента кэша (источник — SQLAlchemy introspection):

```json
{
  "schema": "mgmt_accounting",
  "table": "items",
  "module": "mgmt_accounting",
  "title": "Номенклатура: физический/цифровой товар или услуга",
  "company_scoped": true,
  "platform_only": false,
  "columns": [
    {"name": "id", "type": "UUID", "nullable": false, "pk": true, "fk": null},
    {"name": "company_id", "type": "UUID", "nullable": false, "pk": false,
     "fk": "erp_core.companies.id"},
    {"name": "sku", "type": "VARCHAR(64)", "nullable": false, "pk": false},
    {"name": "sale_price", "type": "NUMERIC(20,4)", "nullable": true, "pk": false},
    {"name": "kind", "type": "VARCHAR(10)", "nullable": false, "pk": false}
  ],
  "masked": ["…"]
}
```

- `title` — первая строка docstring модели (например, `Item.__doc__`,
  `features/inventory/models.py:47-53`); нет docstring — имя таблицы.
- `module` — обратное отображение «схема → модуль» из `MANIFESTS`
  (`src/core/plugins.py:56-62`).
- `company_scoped` — есть колонка `company_id` с FK на `erp_core.companies`.
- `platform_only` — таблицу видят/используют только в платформенном
  контексте: `companies`, `signup_requests` (объекты платформы) и
  платформенный срез `connections` (NULL-строки). Для `connections`
  действует логика Р4 multitenancy: org видит только свои строки
  (`company_id = org`), платформа — все; таблица не `platform_only`, режим
  зависит от контекста.
- В кэш попадают только ORM-описанные таблицы. БД-вьюхи (например,
  `v_stock_balances`) и системные каталоги pg_* не показываются.
- Маскируемые колонки помечаются в кэше по паттернам §9.1.

### 6.2 API

Все эндпоинты — в роутере ядра (префикс `/api/v1`, как у остальных
`system/*`; ядро — единственное место, которому по архитектуре доступен
реестр всех схем):

1. `GET /system/tables` — список таблиц с метаданными (краткий: schema,
   table, module, title, company_scoped, platform_only, masked-колонки).
   Право: `table_browser:ro`. В контексте org платформенные таблицы
   (`companies`, `signup_requests`) не возвращаются; `rates`/`doc_types`/
   `units` возвращаются (глобальные несекретные справочники).
2. `GET /system/tables/{schema}/{table}` — полные метаданные одной таблицы
   (колонки с типами). Право: `table_browser:ro`.
3. `GET /system/tables/{schema}/{table}/rows` — строки. Параметры:
   - `columns` — список имён колонок (по умолчанию все; маскируемые
     допустимы — вернут `***`);
   - `filters` — JSON-массив `[{"col": "...", "op": "...", "value": ...}]`,
     операторы: `eq`, `ne`, `contains` (ILIKE %v%, для чисел/дат — `between`),
     `in` (`value` — массив), `between` (`value`: [от, до]), `is_null`,
     `not_null`. Значения приводятся к типу колонки (UUID/дата/число/
     булево; NUMERIC — Decimal, ADR-003);
   - `sort` — `col,direction` (direction: `asc|desc`), максимум 2 уровня;
   - `limit/offset/format=paginated` — по конвенции `PageParams`
     (`?limit=50&offset=0`, ответ `{"items": [...], "total": N}`).
   Ответ: список словарей «колонка → значение» (Decimal — строкой,
   datetime/date — ISO 8601, UUID — каноничной строкой).
   Право: `table_browser:ro`. Источник — RO-подключение (§9.2).
4. `POST /system/tables/{schema}/{table}/export` — CSV. Тело: те же
   `columns/filters/sort`. Ответ: `text/csv` (StreamingResponse),
   имя файла `{schema}__{table}__YYYYMMDD-HHMM.csv`, разделитель `;`,
   кодировка UTF-8 с BOM (Excel открывает без танцев). Лимит
   `TABLE_EXPORT_ROW_LIMIT = 10 000` строк (env, §9.4): выборка сверх
   лимита → 422 с `total` и сообщением «уточните фильтр» (предупреждение
   до старта выгрузки — повторная проверка в момент экспорта).
   Право: `table_browser:ro`.

### 6.3 Правила применения

- **Автокомпания** (§4): таблица `company_scoped` + org-контекст →
  неявный `company_id = org`. Явный фильтр по `company_id` из запроса —
  422 (нельзя ни расширить, ни сузить чужую область). В платформенном
  контексте явный фильтр по `company_id` разрешён (выбор организации для
  просмотра).
- **Маскируемые колонки**: значение всегда `***`; фильтр или сортировка по
  ним → 422 (защита от оракула побайтового подбора).
- **Без JOIN**: запрос всегда к одной таблице; фильтры — только по её
  колонкам. Связанные имена (например, `account_id` → название счёта) —
  вне v1 (§14).
- Ограничения нагрузки: `limit ≤ 100 000` (как `page_params`), глубина
  `offset` не ограничивается (ключевая пагинация — v2),
  `statement_timeout` на RO-подключении (§9.2).

### 6.4 Аудит просмотров

Каждый вызов `rows` и `export` пишет `AuditEvent`
(`action='system.table_viewed' | 'system.table_exported'`):

```json
{"schema": "...", "table": "...", "filters": [...], "columns": [...],
 "limit": 50, "offset": 0, "rows": 50, "total": 1234, "platform": false}
```

`company_id` аудита — org контекста (или NULL для платформенного, поле
`platform: true` различает). Просмотр метаданных (`GET /system/tables`) не
журналируется (это не доступ к данным). Чтение журналов отладчиком тоже не
журналируется (мета-шум; §8).

### 6.5 Сценарии (Given/When/Then)

Основной:
- G: аналитик с `table_browser` в org «Основная». W: `GET /system/tables/
  mgmt_accounting/items/rows?filters=[{"col":"kind","op":"eq","value":
  "digital"}]&limit=50&format=paginated`. T: 200, только товары своей
  организации, конверт `{items, total}`; в `events_log` —
  `system.table_viewed` с фильтром и числом строк.
- G: тот же аналитик. W: `POST .../items/export` c фильтром по `kind`.
  T: 200 `text/csv` c BOM; в файле нет колонок `company_id`-значений чужих
  организаций; аудит `system.table_exported`.

Альтернативные:
- G: платформенный админ без выбранной org. W: `GET /system/tables/
  erp_core/users/rows`. T: 200, строки всех организаций;
  `password_hash` = `***`; аудит с `platform: true`.
- G: платформенный админ. W: фильтр `company_id = <uuid>`. T: 200 — явный
  выбор организации допустим только в платформенном контексте.

Ошибочные:
- G: пользователь без `table_browser`. W: любой `/system/tables/*`.
  T: **403**; в `/me/permissions` ключа с уровнем ≥ ro нет; раздел UI не
  появляется.
- G: аналитик org A. W: `filters=[{"col":"company_id","op":"eq",
  "value":"<uuid org B>"}]`. T: **422** (company_id — служебная колонка
  области).
- G: аналитик. W: `filters=[{"col":"password_hash","op":"contains",
  "value":"gAAA"}]` на `erp_core.users`. T: **422** (маскируемая колонка).
- G: аналитик. W: несуществующая таблица/колонка, либо `col = "id; DROP
  TABLE users"` (инъекция). T: **422** — сверка с кэшем метаданных до
  построения SQL.
- G: аналитик. W: export с `total = 48 000` при лимите 10 000. T: **422**
  c `total` и сообщением; файла нет, аудит не пишется (попытка — на
  усмотрение, v1: не пишется).

## 7. Этап B — ракурсы ведения

### 7.1 Модель данных

Таблица `erp_core.maintenance_views` (миграция §10):

| колонка | тип | примечание |
|---|---|---|
| `id` | UUID PK | |
| `company_id` | UUID NULL FK `erp_core.companies` | NULL = платформенный шаблон |
| `name` | String(100) | UNIQUE `(company_id, name)` NULLS NOT DISTINCT (PG16) |
| `table_schema` | String(63) | |
| `table_name` | String(63) | |
| `mode` | String(10) | `domain` \| `direct` |
| `definition` | JSONB | см. ниже |
| `is_active` | Boolean default true | |
| `created_by` | UUID FK users | |
| `created_at` / `updated_at` | DateTime(tz) | |

`definition` (JSONB):

```json
{
  "columns": [
    {"name": "name", "visible": true, "editable": true},
    {"name": "inn",  "visible": true, "editable": true},
    {"name": "internal_code", "visible": true, "editable": false}
  ],
  "where": [{"col": "is_transit", "op": "eq", "value": false}],
  "order_by": [{"col": "name", "dir": "asc"}],
  "validations": [
    {"col": "inn", "rule": "regex", "value": "^\\d{10,12}$"},
    {"col": "name", "rule": "required"}
  ],
  "domain": {
    "api": {
      "list":   "GET  /api/v1/accounting/counterparties",
      "create": "POST /api/v1/accounting/counterparties",
      "update": "PATCH /api/v1/accounting/counterparties/{id}"
    },
    "field_map": {"name": "name", "inn": "inn", "kpp": "kpp"}
  },
  "access": "company"
}
```

- `where` — фиксированный фильтр области (для guarded-таблиц обязателен,
  см. §7.3); пользователь не может его обойти или расширить.
- `domain` — только в режиме domain; `field_map` — колонка таблицы → поле
  API-схемы.
- `access`: `"company"` — все обладатели `maint_views` организации
  (default); `{"users": ["<uuid>", ...]}` — узкий круг (проверка при
  каждом обращении).
- **Аудит изменений ракурса**: create/update/delete → `events_log`
  (`system.view.created/updated/deleted`, payload — diff определения) +
  `record_versions` (`entity_type='maintenance_view'`, diff). Владелец
  требует аудируемость конфигурации — она важнее самих данных.

### 7.2 Режимы

**Domain** — для сущностей с бизнес-логикой. Ракурс декларирует маппинг на
публичный API модуля; исполнение — вызов **сервисного слоя модуля в
процессе** через реестр адаптеров: модуль регистрирует в манифесте
`devtools_adapters: {"counterparty": {list, create, update}}`
(расширение `Manifest`, `src/core/contracts.py`; подключение — по образцу
`event_handlers` в `plugins.py:65-83`). Прямой HTTP-вызов самому себе
запрещён (сеть — только коннекторы, ADR-001); прямой импорт модуля из
ядра — нарушает архитектуру модулей; реестр адаптеров сохраняет инверсию
зависимостей. В `definition.domain.api` пути указываются для документации
и UI (это публичный контракт), исполнение идёт адаптером.

**Direct** — генерический CRUD для таблиц белого списка (§7.3):
- `company_id` проставляется автоматически из org-контекста и
  **нередактируем**: колонка исключается из `editable`; передача её в теле
  правки/создания → 422. Для платформенных таблиц (без `company_id`)
  ракурс доступен только платформенному контексту.
- Запрет «документов двойной записи»: ракурсы НЕ создаются на таблицы
  чёрного списка §7.3 — редактирование документов только через доменные
  API (проведение/сторно/резервы/средняя себестоимость — целостность
  ресурсов, ADR-007).
- Правка строки: одна транзакция — проверка `editable`-колонок →
  декларативные `validations` → UPDATE (ORM по PK) →
  `record_version(db, "<table>", pk, user, diff, reason="maintenance_view")`
  → `AuditEvent('system.view_row_updated', payload={view_id, table, pk,
  changed: [cols]})`.
- Создание строки: аналогично, `system.view_row_created`.
- Удаление строк — вне v1 (§14): где есть `is_active`, «выключение» —
  правка булевой колонки.

### 7.3 Списки таблиц (в коде, не в БД)

`MAINT_VIEW_FORBIDDEN` — чёрный список (создание ракурса → 422):
- документы двойной записи и их строки: `transactions`, `stock_moves`,
  `item_serials`, `purchase_orders`, `purchase_order_lines`, `receipts`,
  `receipt_lines`, `sales_orders`, `sales_order_lines`, `shipments`,
  `shipment_lines`, `tech_cards`, `production_orders`;
- деньги/потоки: `online_payments`, `flow_runs`, `webhook_events`;
- секреты и доступы: `users`, `auth_sessions`, `revoked_tokens`,
  `password_resets`, `signup_requests`, `user_totp`, `totp_backup_codes`,
  `api_tokens`, `connections`, `webhook_endpoints`, `settings`;
- журналы и инфраструктура: `events_log`, `record_versions`,
  `event_outbox`, `module_registry`, `backups`, `doc_sequences`,
  `sync_runs`, `periods`.

`DIRECT_ALLOWED` — белый список direct-режима (расширение — только правкой
кода с ревью):

| таблица | контекст | ограничения |
|---|---|---|
| `mgmt_accounting.categories` | org | без `parent_id`-циклов не проверять — поле нередактируемо в v1 |
| `mgmt_accounting.locations` | org | обязательный `where: is_transit = false` |
| `mgmt_accounting.units` | платформа | глобальный справочник |
| `mgmt_accounting.doc_types` | платформа | глобальный; правка `number_prefix` влияет на нумерацию — предупреждение в UI |

`rates` сознательно НЕ в белом списке: у курсов есть доменный API
(`POST /rates`, `mgmt_accounting/router.py:542`) и семантика заморозки
курсов (ADR-003) — правка истории курсов только через домен.

`DOMAIN_ADAPTERS` — реестр domain-режима (регистрируют модули):
- этап B: `mgmt_accounting` — контрагент (`counterparties`).
  **Prerequisite**: добавить `PATCH /accounting/counterparties/{id}`
  (сейчас нет — `router.py:277-347`) с валидацией дублей ИНН+КПП,
  `record_versions`, аудитом; это небольшая доменная задача внутри этапа B.
- кандидат №2: `mini_crm.deals` (PATCH уже есть, `mini_crm/router.py:240`)
  — по запросу пилотов, не блокирует этап.

### 7.4 API

- `GET /system/maintenance-views` — список видимых: своей организации +
  платформенные шаблоны (только метаданные, `definition` — для rw).
  Право: `maint_views:ro`.
- `POST /system/maintenance-views` — создать (валидация: таблица не в
  чёрном списке; direct — только белый список; guarded-условия; режим
  соответствует таблице). Право: `maint_views:rw`; `company_id` — из
  контекста (принудительно). Платформенный шаблон — только
  платформенный контекст.
- `PATCH /system/maintenance-views/{id}` — изменить определение/`is_active`.
  Своей организации — `maint_views:rw`; шаблон — только платформа.
  `PUT` семантики нет (правка полная, diff в аудит).
- `DELETE /system/maintenance-views/{id}` — удалить конфигурацию (данные
  не трогаются). Права как у PATCH.
- `GET /system/maintenance-views/{id}/rows` — чтение строк ракурса:
  метаданные колонок + данные с `where`-фильтром области, автокомпанией,
  фильтрами/сортировкой/пагинацией как в браузере (те же правила §6.3).
  Право: `maint_views:ro` + `access`.
- `POST /system/maintenance-views/{id}/rows` — создать строку (тело:
  `{col: value}` по `editable`). Право: `maint_views:rw` + `access`.
- `PATCH /system/maintenance-views/{id}/rows/{pk}` — правка строки
  (`pk` — значение PK-колонки; v1 — таблицы с единственным PK).
  Право: `maint_views:rw` + `access`.

### 7.5 Сценарии (Given/When/Then)

Основной:
- G: аналитик с `maint_views:rw`. W: создаёт direct-ракурс «Статьи затрат»
  на `mgmt_accounting.categories` (editable: `name`, `kind`). T: 201;
  `events_log system.view.created` + `record_versions
  entity_type='maintenance_view'`.
- G: ракурс создан. W: `PATCH .../rows/{pk}` `{"name": "Транспорт"}`.
  T: 200; в БД новая версия `record_versions
  entity_type='categories', reason='maintenance_view'`, diff
  `{"name": {"old": "Транспорт", "new": "Транспорт"}}`; аудит
  `system.view_row_updated`.

Альтернативные:
- G: domain-ракурс «Контрагенты» (адаптер учёта). W: правка `inn` на
  дубликат существующего контрагента. T: 422 от доменной валидации дублей
  (ИИН+КПП) — ракурс не обходит бизнес-правила; версия не пишется
  (изменения нет).
- G: организация использует платформенный шаблон. W: правка шаблона из
  org-контекста. T: **403** (шаблоны правит только платформа); «Клонировать»
  (GET + POST своей копии) — доступно.

Ошибочные (обязательные тесты):
- G: `maint_views:rw`. W: создать ракурс на `mgmt_accounting.transactions`
  или `stock_moves`. T: **422** `table_forbidden` — чёрный список §7.3.
- G: direct-ракурс на `categories`. W: `PATCH .../rows/{pk}`
  `{"company_id": "<uuid другой org>"}`. T: **422** — `company_id`
  нередактируем; строка не менялась.
- G: ракурс на `locations` без `where is_transit = false`. W: создание.
  T: **422** `guarded_filter_required`.
- G: `maint_views:ro` (без rw). W: `PATCH .../rows/{pk}`. T: **403**.
- G: ракурс с `access: {"users": [...]}`, вызывающий не в списке. W:
  чтение строк. T: **403** `view_access_denied`.
- G: правка через direct-ракурс колонки, не входящей в `editable`. T:
  **422**; правка колонки из `editable`, но провалившая `validations`
  (regex ИНН) — 422 с именем колонки и правилом.

## 8. Этап C — отладчик

Наблюдаемость, БЕЗ исполнения кода/SQL. Три блока:

### 8.1 Единая консоль журналов

`GET /devtools/logs` — параметр `source` выбирает источник (объединение в
один ответ — на клиенте переключением вкладки; сервер единым списком не
мерджит, чтобы не плодить кастомные сортировки):

| source | таблица | фильтры | org-изоляция |
|---|---|---|---|
| `audit` | `erp_core.events_log` | `action` (префикс), `user_id`, `entity_type`, `date_from/to`, `q` (по payload ILIKE) | `company_id = org`; платформа — все |
| `egress` | `events_log` c `action='egress.request'` | `host` (payload), `status`, даты | egress-записи без company_id → только платформа |
| `outbox` | `erp_core.event_outbox` | `event_name`, `processed`, даты | `payload->>'company_id' = org`; платформа — все |
| `sync` | `integrations.sync_runs` (+ имя job) | `status`, `job_id`, даты | join `sync_jobs.company_id` |
| `flow` | `integrations.flow_runs` (+ payment) | `status`, `step`, `error` (ILIKE), даты | join `online_payments.company_id` |
| `webhooks` | `integrations.webhook_events` | `status`, `event_type`, даты | join `webhook_endpoints.company_id` |

Пагинация — `PageParams` (`{items, total}`), сортировка по времени desc.
Маскировка: `payload` отдаётся как записан (журналы пишутся без секретов
по коду — `egress.py:100-127`); поле `buyer` из `online_payments` не
показывается (ПДн, срез ro-роли маскирует и API модуля). Право:
`devtools:ro`. Чтение журналов не журналируется.

### 8.2 Трассировка документа

`GET /devtools/trace/{entity_type}/{entity_id}` — таймлайн жизни одной
сущности, три источника в одном хронологическом списке (desc, limit 200):

1. `record_versions` по `(entity_type, entity_id)` — кто/когда/какие поля
   (diff уже в JSONB);
2. `events_log` по `(entity_type, entity_id)` — аудиторские действия;
3. `event_outbox` — события шины, где `payload->>'entity_id'` или
   `payload->>'id'` равны `entity_id` (события `acc.*`/`crm.*` кладут id
   в payload; JSONB-путь без индекса — на объёмах МСБ допустимо, GIN-индекс
   — опция по нагрузке, открытый вопрос О9).

`entity_type` — строковые ключи модулей: `acc.transaction`,
`acc.sales.order`, `crm.deal`, `user`, `maintenance_view` (примеры —
вызовы `record_version(...)` в сервисных слоях). Ответ:
`{entity_type, entity_id, timeline: [{at, source: "version"|"audit"|
"outbox", ...}]}`. Пример использования: «откуда взялась транзакция
ПК-2026-00001» — версии (создание/проведение), аудит (кто проводил),
событие `acc.transaction.posted` из outbox. Право: `devtools:ro`;
org-изоляция как у соответствующих журналов.

### 8.3 Диагностика

`GET /devtools/diagnostics` — агрегат (только чтение, без мутаций):

- **Версии**: приложение (`src.__version__`), реестр модулей
  (`module_registry`: имя/версия/схема/активность), доступное обновление
  (переиспользовать `db_latest_update_info()` из `GET /system/version`,
  `router.py:2148`).
- **Здоровье**: БД (ping + latency), Redis (ping), outbox (количество и
  возраст нераспределённых `processed=false`), последние падения
  `sync_runs` (status=failed, топ-10) и `flow_runs` (failed/manual).
- **Коннекторы**: список подключений (`name`, `connector_code`,
  `last_check_at`, `last_check_ok`) без секретов. Тест связи — кнопка,
  вызывающая существующий `POST /integrations/connections/{id}/test`
  (не дублируем; требует прав интеграций — если прав нет, кнопка скрыта).

Право: `devtools:ro`. org-контекст — своё; платформенный — вся установка.

### 8.4 Сценарии

- G: упала синхронизация Ozon. W: разработчик открывает «Журналы →
  Синхронизации», фильтр `status=failed`. T: строки с `error`, имя job,
  время; клик по шагам `flow_runs` показывает `step`/`attempts`/`error`.
- G: пилот спрашивает «кто поменял сумму в ПК-2026-00042». W:
  `GET /devtools/trace/acc.transaction/{id}`. T: таймлайн: создание →
  правка (diff суммы, пользователь) → проведение; событие
  `acc.transaction.posted` в outbox.
- G: воркер «завис». W: «Диагностика». T: outbox: pending=37, старейшая
  2 ч назад → подозрение на воркер; Redis/DB — зелёные.
- Ошибочный: G: пользователь без `devtools`. W: `/devtools/*`. T: **403**;
  раздел UI скрыт.

## 9. Безопасность (сводно)

### 9.1 Маскируемые колонки

Паттерны имён (владелец): `*_enc`, `password*`, `token*`, `secret*`,
`totp_*`. Фактические колонки по коду (маска `***` в rows/export/трассировке;
фильтр и сортировка по ним — 422):

- `erp_core.users.password_hash`;
- `erp_core.signup_requests.password_hash`, `token_hash`, `token_enc`;
- `erp_core.password_resets.token_hash`, `token_enc`;
- `erp_core.user_totp.secret_enc`;
- `erp_core.api_tokens.token_hash`;
- `integrations.connections.credentials_enc`;
- `integrations.webhook_endpoints.secret_token`;
- `mgmt_accounting.item_serials.code_enc` (цифровой код — актив).

Список вычисляется из паттернов по кэшу метаданных при старте; дополнения —
только правкой константы паттернов. `item_serials.code_hash` (sha256-отпечаток)
не маскируется — он не обратим и уже показывается ro-ролям缩短 как
`code_hash[:8]` (реестр, фикс-пакет гейта).

### 9.2 RO-подключение к БД

- Новая роль `erp_ro` (миграция §10): `GRANT USAGE ON SCHEMA erp_core,
  integrations, mgmt_accounting, mini_crm, ai_agent` + `GRANT SELECT ON ALL
  TABLES` + `ALTER DEFAULT PRIVILEGES` (чтобы новые таблицы модулей
  покрывались). Пароль — из env `DATABASE_URL_RO`; дев-дефолт
  `erp_ro:erp_ro` (как `erp:erp` в `docker-compose.yml:8-10`), прод обязан
  сменить (запись в README безопасности).
- Второй engine в `src/db.py`: `engine_ro = create_engine(
  settings.database_url_ro, pool_pre_ping=True, connect_args={
  "options": "-c statement_timeout=15000 -c default_transaction_read_only=on"})`.
  Двойная защита: права роли + `default_transaction_read_only` на соединении;
  `statement_timeout` режет тяжёлые фильтры.
- Все запросы браузера (`rows`) идут через `engine_ro`. Если
  `DATABASE_URL_RO` не задан (дев) — используем основной URL с WARNING в
  лог (совместимость дев-контура; в проде отсутствие переменной — фиксировать
  стартовым предупреждением в `/devtools/diagnostics`).
- Мутации ракурсов идут через основную ORM-сессию — это операции API-слоя.

### 9.3 Инъекции и идентификаторы

Только `text()` с bound-параметрами для значений; имена схемы/таблицы/
колонок — сверка с кэшем метаданных (строгий white-list), любое несовпадение
→ 422. `limit/offset` — int-clamped. Операторы — enum. JSONB-значения
фильтров не интерпретируются как SQL.

### 9.4 Лимиты

- `rows`: `limit ≤ 100 000` (конвенция пагинации), `statement_timeout` 15 c.
- export: `TABLE_EXPORT_ROW_LIMIT = 10 000` (env, дефолт); превышение — 422
  с `total` (предупреждение в UI до выгрузки, серверная проверка обязательна).
- трассировка: limit 200.

### 9.5 Аудит

Действия (все — `events_log`, payload без секретов):

| action | когда |
|---|---|
| `system.table_viewed` | каждый запрос rows (§6.4) |
| `system.table_exported` | каждый экспорт CSV |
| `system.view.created / updated / deleted` | жизненный цикл ракурса (+ record_versions) |
| `system.view_row_created / view_row_updated` | правки данных через ракурс (+ record_versions строки) |

Просмотр журналов/трассировка/диагностика — не журналируются (чтение
метаданных и журналов; иначе events_log удваивается каждые пару кликов).
Пересмотр — открытый вопрос О6.

## 10. Модель данных и миграции

Миграция `0037_devtools.py` (следующая за `0036_wb_tables.py`), обратимая:

1. `erp_core.maintenance_views` — таблица §7.1 (+ индексы `company_id`,
   `(company_id, name)` NULLS NOT DISTINCT). Downgrade: drop table.
2. PG-роль `erp_ro` с грантами §9.2 (`CREATE ROLE ... LOGIN PASSWORD
   'erp_ro'` в дев; `IF NOT EXISTS`-семантика через DO-блок). Downgrade:
   revoke + drop role. Пароль прода — вне миграции (env).
3. Без изменения существующих таблиц. `user_permissions` используется как
   есть (новые значения `module` — данные, не DDL; CHECK на `module` в БД
   нет — модель ограничивает длиной, справочник `MODULES` — в коде).

Конфигурация: `DATABASE_URL_RO`, `TABLE_EXPORT_ROW_LIMIT` в `Settings`
(`src/config.py`) + `.env.example` + `docker-compose.yml` (сервисы
api/worker).

События шины: **не публикуем** (инструменты ядра; аудит в events_log
достаточен — как в role-delegation §6). Если у пилотов появится спрос на
уведомления «ракурс изменён» — v2, опцией outbox.

## 11. UI

Раздел «Система → Инструменты» (`/tools`, навигация — в секцию «Система»,
`ErpShell.vue:84-89`; пункт виден при любом из трёх прав > none по
`/me/permissions`). Пять вкладок; каждая дополнительно гейтится своим
правом (нет права — вкладки нет; всех прав нет — раздела нет):

1. **Таблицы** (`table_browser`): слева — дерево «модуль → таблицы» (из
   `GET /system/tables`, поиск по имени/заголовку); справа — грид
   (DataTable): чекбокс колонок, панели «Фильтры» (колонка → оператор →
   значение; операторы по типу колонки) и «Сортировка»; список строк в
   `PaginatedList` (порция 50, счётчик «Показано X из Y»); кнопка
   «Экспорт CSV» (перед выгрузкой — предупреждение при total > лимита,
   Toast об ошибке 422 с числом). Маскируемые колонки — серые, значение
   `***`, бейдж «секрет».
2. **Ракурсы** (`maint_views`): список ракурсов (PaginatedList, свои +
   шаблоны с бейджем «Шаблон платформы»); редактор — диалог-форма из
   `definition`: выбор таблицы (селект из допустимых — чёрный список
   исключён на сервере, клиент дублирует для UX), чекбоксы «видимая/
   редактируемая» по колонкам, фиксированный фильтр области, простые
   валидации, режим (direct/domain — выбор ограничен белым списком/
   адаптерами). Свёрнутый блок «Определение JSON» — только чтение
   (копирование для поддержки). Правка строк — в том же гриде
   (двойной клик по editable-колонке), сохранение — PATCH rows.
3. **Журналы** (`devtools`): выбор источника (табы: Аудит / Egress /
   Шина / Синхронизации / Флоу / Вебхуки), фильтры, таблица в
   PaginatedList; строки разворачиваются (payload JSON).
4. **Трассировка** (`devtools`): форма `entity_type` (селект известных
   ключей + свободный ввод) + `entity_id` → вертикальный таймлайн
   (источник цветом: версия/аудит/событие).
5. **Диагностика** (`devtools`): карточки-статусы (БД/Redis/outbox),
   версии (приложение/модули), последние падения синхронизаций/флоу,
   список коннекторов со статусом последней проверки.

Общее: i18n-ключи ru с первого дня (`tools.*`), деньги/количества —
строки, никаких float (ADR-003); компоненты существующего UI-кита
(DataTable, Dialog, Tabs, StatusBadge); после каждого коммита с
`frontend/**` — пересборка web (правило реестра).

## 12. Влияние

- **Ядро**: `auth.py` (расширение `MODULES`), `router.py` (новые
  `/system/tables*`, `/system/maintenance-views*`, `/devtools/*`),
  `db.py` (engine_ro), `config.py` (2 настройки), `contracts.py`
  (`Manifest.devtools_adapters`), новый пакет `src/core/devtools/`
  (метакэш, построитель фильтров, RO-движок, ракурсы) — слои по ADR-005.
- **Модули**: не трогаются, кроме (а) регистрации адаптеров в манифестах
  (mgmt_accounting — контрагент, этап B), (б) prerequisite PATCH
  контрагентов в mgmt_accounting. Изоляция схём соблюдена: чтение —
  introspection+SELECT, запись — только через доменные сервисы/белый список.
- **RBAC**: три новых ключа модулей; матрица ролей получит три строки
  (отображение «Инструменты: Браузер таблиц / Ракурсы / Отладчик»),
  встроенные роли — none; делегирование/каскад/аудит — без изменений кода.
- **Шина**: без изменений (событий не добавляем). Трассировка читает
  outbox.
- **Фронтенд**: новый раздел + 5 вкладок; навигация из `/me/permissions`.
- **Безопасность**: новые поверхности (массовое чтение таблиц) закрыты
  правами, автокомпанией, маскировкой, RO-ролью, лимитами, аудитом;
  каждый просмотр данных — строка в events_log (требование владельца).
- **Тесты**: блок `tests/test_devtools.py` (права, маскировка, инъекции,
  автокомпания, лимиты, чёрный/белый списки, версии ракурсов) + smoke-
  проверка «браузер + ракурс + журнал».

## 13. Этапы, объём, критерии приёмки

### Этап A — браузер таблиц (~1,5 недели: бэкенд 3–4 дня, фронт 2–3,
тесты/полировка 1–2)

Состав: метакэш + `GET /system/tables[/...]`, `rows` + фильтры/сортировка/
пагинация, CSV-экспорт с лимитом, маскировка, RO-роль и engine_ro,
автокомпания, аудит просмотров, привилегия `table_browser` (MODULES +
выдача), UI-вкладка «Таблицы».

Приёмка (чек-лист для QA):
1. Пользователь без `table_browser`: `GET /system/tables` → 403; пункт
   меню «Инструменты» не появляется; `/me/permissions` — none.
2. Выдача гранта rw-обладателем (делегирование) — доступ появляется без
   перелогина (права читаются из БД).
3. Аналитик org A: `items` своей org; прямого попадания строк org B нет
   (проверить total против psql-среза по company_id).
4. `erp_core.users`: `password_hash` = `***` в rows и в CSV; фильтр и
   sort по `password_hash` → 422. Аналогично `connections.credentials_enc`,
   `webhook_endpoints.secret_token`, `item_serials.code_enc`.
5. Явный фильтр по `company_id` в org-контексте → 422; в платформенном
   контексте — работает.
6. Платформенный контекст: `companies`/`signup_requests` видны;
   org-контекст: не возвращаются в `GET /system/tables`.
7. Инъекционные имена (`col: "id; DROP TABLE users"`, несуществующая
   таблица) → 422; после серии попыток схема цела.
8. Экспорт: 9 999 строк — файл «;»-CSV с BOM; 10 001 → 422 с total.
9. Каждый вызов rows/export — строка `system.table_viewed/exported` в
   events_log (кто/таблица/фильтры/строки).
10. `pytest` + `ruff` зелёные; RO-роль не может выполнить UPDATE
    (проверка SQL-сессией от erp_ro: `ERROR: read-only`).

### Этап B — ракурсы ведения (~2 недели: модель+CRUD 2–3 дня, direct-движок
с валидациями/версиями 2–3, domain-адаптер + PATCH контрагентов 1–2,
UI-редактор 2–3, тесты 1–2)

Состав: `maintenance_views` + CRUD конфигурации с аудитом; direct-движок
(белый список: categories, locations, units, doc_types); реестр адаптеров
+ доменный PATCH контрагентов; rows API (чтение/правка/создание);
привилегия `maint_views`; UI-вкладка «Ракурсы».

Приёмка:
1. Создание ракурса на `transactions`/`stock_moves`/`shipments` → 422
   (чёрный список); на таблицу не из белого списка в direct-режиме → 422.
2. Direct-ракурс на `categories`: правка `name` → 200, в `record_versions`
   появилась версия `entity_type='categories'` с diff и
   `reason='maintenance_view'`; в events_log — `system.view_row_updated`.
3. Попытка изменить `company_id` через direct-ракурс → 422, строка не
   изменилась.
4. Создание строки через direct-ракурс: `company_id` проставлен
   автоматически = org контекста.
5. Ракурс на `locations` без `where is_transit=false` → 422
   (`guarded_filter_required`); с фильтром — транзитные не видны и недоступны.
6. Domain-ракурс «Контрагенты»: правка ИНН на дубль → 422 от доменной
   валидации; успешная правка пишет версии/аудит.
7. Платформенный шаблон: виден организации, правка из org → 403;
   правка платформенным контекстом → 200.
8. `maint_views:ro` — чтение есть, правки строк и конфигурации → 403.
9. Изменение самого ракурса — `record_versions` c `entity_type=
   'maintenance_view'` и `events_log system.view.updated`.
10. Платформенные direct-таблицы (`units`, `doc_types`) из org-контекста
    → 403/404 (ракурсы только платформенные).

### Этап C — отладчик (~1 неделя: журналы 2 дня, трассировка 1–2,
диагностика 1, UI 1–2)

Состав: `GET /devtools/logs` (6 источников, фильтры, пагинация),
`GET /devtools/trace/...`, `GET /devtools/diagnostics`, привилегия
`devtools`, UI-вкладки «Журналы/Трассировка/Диагностика».

Приёмка:
1. Без `devtools` — все `/devtools/*` → 403, вкладок нет.
2. Org-изоляция: аналитик org A в `source=audit` не видит события
   company_id org B; `source=sync` — только свои jobs.
3. Трассировка известного документа (например, проведённая транзакция):
   таймлайн содержит версию создания, версию проведения, аудит и событие
   outbox `acc.transaction.posted`.
4. Фильтры журналов работают (status=failed у sync, error ILIKE у flow);
   пагинация total корректен.
5. Диагностика показывает версию приложения, модули, БД/Redis статусы,
   outbox pending; секреты подключений отсутствуют в ответе.
6. Журналы не замусориваются чтением отладчика (нет self-audit-петель).

## 14. Что НЕ входит в эту версию

- **SQL-консоль** (даже read-only с дев-флагом — решение арх-чата
  2026-09-10 вынесено за скобки; отдельный ADR-кандидат при спросе).
- **JOIN/связанные колонки** в браузере (владелец: без JOIN); батч-lookup
  имён (`account_id` → имя счёта) — кандидат v2 по нагрузке на пилотах.
- **Удаление строк** через ракурсы (SM30 умеет; v1 — только правка/создание;
  где есть `is_active` — «выключение» правкой).
- **Экспорт в Excel** (xlsx) — только CSV с BOM (открытый вопрос О1).
- **Сохранённые фильтры/макеты** браузера (layout-варианты SE16N) —
  ракурсы частично закрывают потребность; отдельная фича v2 (О2).
- Составные PK в ракурсах; ключевая (keyset) пагинация браузера.
- Массовые операции в ракурсах (SM30 «массовое изменение»).
- Правка `rates` в direct-режиме (только доменный API, §7.3).
- Редактирование таблиц документов двойной записи — запрещено навсегда,
  не «отложено»: целостность проведения/сторно/средней себестоимости
  (ADR-007) несовместима с генерическим UPDATE.
- GIN-индекс по payload outbox для трассировки (по нагрузке).
- События инструментов в шину; уведомления об изменениях ракурсов.
- Инструменты для API-токенов (личных грантов у токенов нет — граница
  делегирования v1).

## 15. Открытые вопросы (с рекомендациями)

- **О1. Экспорт в Excel?** CSV с «;» и BOM открывается в Excel/Либре,
  лист до 1 млн строк. Рекомендация: v2 по запросу пилотов (xlsx-библиотека
  в бандл, вес образа).
- **О2. Сохранённые фильтры/макеты браузера (аналог layout SE16N)?**
  Рекомендация: v2 как личные пресеты в `ai_agent.user_settings`-паттерне
  (key-value на пользователя); в v1 потребность «повторяемого среза»
  закрывают ракурсы с фиксированным фильтром.
- **О3. Кто может создавать ракурсы?** Сейчас: `maint_views:rw` (обычно
  админ/аналитик-наставник), платформенные шаблоны — только платформа.
  Рекомендация: оставить так; если пилоты попросят «каждый ведёт свои
  ракурсы» — расширить `access` до личных ракурсов (v2).
- **О4. Журналирование каждого page-запроса rows** даст 3–5 строк аудита
  на сессию просмотра (пагинация). Рекомендация: v1 — писать каждый вызов
  (требование владельца «каждый просмотр»); дедупликация «тот же фильтр
  той же сессии 60 c» — полировка по факту объёмов events_log.
- **О5. Расширение белого списка direct-таблиц** — процесс? Рекомендация:
  только PR с ревью архитектурного чата (список — код, §7.3); критерий
  включения — у таблицы нет бизнес-инвариантов вне constraints БД.
- **О6. Аудит чтения журналов отладчика** (сейчас — нет). Рекомендация:
  не вводить (мета-шум); пересмотреть, если появится сценарий «кто видел
  ПДн в журналах» — тогда флаг в настройках.
- **О7. Открыть ли браузер на `item_serials.code_hash` для org-аналитика?**
  Уже открыт (не маскируется) — отпечаток не обратим и используется ro-
  ролями. Оставить; закрыть, если служба безопасности пилота попросит.
- **О8. Показ «чужих» имён в гриде браузера** (UUID без расшифровки
  неудобны). Рекомендация: v1 без lookup; v2 — батч-подстановка имён по
  FK-метаданным (регламент пагинации: имена в payload, не отдельными
  запросами).
- **О9. Производительность трассировки outbox** (JSONB-путь без индекса).
  Рекомендация: замерить на мини-заезде; при >100k событий — частичный
  индекс по `event_name` + ограничение трассировки последними N событиями.
- **О10. Второй domain-адаптер (deals CRM) в этап B?** Рекомендация: нет —
  этап B закрывается контрагентами; deals добавить по спросу (PATCH уже
  есть, адаптер дёшев).

---

### Приложение: примеры колонок для калибровки UI (из кода)

- `erp_core.users` (`src/core/models.py:21-47`): `id`, `email`,
  `password_hash` (маскируется), `full_name`, `role`, `is_active`,
  `token_version`, `company_id`, `is_platform_admin`,
  `totp_setup_deadline`, `username`, `must_change_password_by`,
  `created_at`.
- `mgmt_accounting.items` (`features/inventory/models.py:46-76`): `id`,
  `company_id`, `sku`, `name`, `kind`, `unit_code`, `tracking`, `barcode`,
  `sale_price`, `avg_cost`, `low_stock_threshold`, `is_active`,
  `created_at`, `updated_at`.
- `mini_crm.deals` (`src/modules/mini_crm/models.py:42-75`): `id`,
  `company_id`, `title`, `stage_id`, `counterparty_id`, `contact_id`,
  `responsible_id`, `amount`, `currency`, `rate`, `amount_base`,
  `expected_close_at`, `won_at`, `lost_at`, `lost_reason`, `dimensions`,
  `is_deleted`, `created_by`, `created_at`, `updated_at`.
