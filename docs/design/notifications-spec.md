# Спека: серверные уведомления — персистентный центр уведомлений

Статус: **ПРОЕКТ** (на ревью архитектурного чата) · Дата: 2026-10-05 ·
Чат: архитектурный (задание — «серверные уведомления»; предыстория:
frontend-redesign §5.1 v1-lite «Центр уведомлений» с пометкой «серверная
версия — этап H»; Tauri-оболочка сотрудников — v1.1, решения реестра
2026-09-19 и `docs/design/box-installer-spec.md`)

## 1. Контекст и проблема

Центр уведомлений сегодня целиком клиентский: колокольчик в шапке
(`frontend/src/layouts/NotificationCenter.vue`, монтируется в
`frontend/src/layouts/ErpShell.vue:311`) при загрузке и раз в 60 секунд
опрашивает три готовых API и собирает список в памяти вкладки. Боли:

- **Прочитанность живёт в localStorage** (ключ `erp-read:{тип:entity:дата}`):
  перезагрузка на другом устройстве/браузере — все уведомления снова
  «новые»; идентификатор включает дату — на следующий день уже
  прочитанное служебное событие «оживает».
- **Нет доставки «когда закрыто»**: poll существует только в открытой
  вкладке; синхронизация упала ночью — утром об этом знает только тот,
  кто открыл систему и дождался опроса.
- **Нет истории**: что падало на прошлой неделе — нигде не хранится.
- **Адресация случайная**: одни и те же сигналы видит любой вошедший
  (включая readonly-сотрудника без прав на интеграции), при этом
  платформенный админ — никто (заявки на регистрацию приходят ему
  письмом, `src/core/signup.py:117-129`).

Параллельно в системе уже есть канал Telegram: правила
`integrations.notification_rules` + отправка из Celery-воркера. Два
механизма «событие → сообщение» не связаны: in-app нет вообще,
Telegram не видно в UI центра.

Цель: уведомление становится **записью в БД с адресатом** (пользователь
или роль организации), центр читает сервер, прочитанность
персистентна, генерация — из событий шины и периодических проверок
(beat), Telegram-правила присоединяются к той же точке «условие →
каналы». Контракт переиспользуется нативной Tauri-оболочкой (тосты,
v1.1).

Целевые пользователи: руководитель/админ организации (просрочки,
падения синхронизаций, онлайн-заказы, заявки), сотрудник (личные
напоминания: дедлайн 2FA, просроченные задачи CRM), платформенный
админ (заявки на подключение, состояние бэкапов установки).

## 2. Термины

- **Уведомление (notification)** — персональная запись в
  `erp_core.notifications`: тип, важность, заголовок, текст, ссылка на
  сущность, `read_at`. Атом адресации — конкретный `user_id`.
- **Тип (kind)** — строковый ключ из реестра типов в коде ядра
  (`low_stock`, `sync_failed`, …). Реестр — единое место «какие
  уведомления вообще бывают».
- **Аудитория (audience)** — правило адресации при создании:
  `user` (конкретный пользователь), `admins` (админы организации),
  `all` (все пользователи организации), `platform_admins` (платформенные
  админы). После создания аудитория разворачивается в строки (fan-out,
  §5.2) и хранится на строке как происхождение.
- **Канал (channel)** — способ доставки: `in_app` (центр уведомлений,
  эта спека), `telegram` (правила `notification_rules`), `email`
  (вне скоупа, открытый вопрос О3).
- **dedup_key** — детерминированный ключ схлопывания повторов: одно
  условие (например, «товар X ниже порога» в пределах дня) даёт одну
  строку на получателя, сколько бы проверок/событий ни прошло.
- **Периодика (sweep)** — задача Celery по расписанию beat, проверяющая
  условие запросом к БД (остатки, дедлайны, бэкапы) и создающая
  уведомления с дневным dedup_key.
- **Потребитель событий (consumer)** — подписчик шины ядра, который по
  факту события (`integration.sync.failed` и др.) создаёт уведомления.
- Используются термины проекта: событийная шина/outbox, диспетчер
  (`dispatch_outbox`), Celery worker/beat, организация/тенант,
  платформенный админ (`is_platform_admin`), `CompanyScoped`,
  `PageParams`/конверт `{items, total}`, `events_log`, `sync_runs`,
  `flow_runs`, `notification_rules`.

## 3. As-is (ссылки на код)

### 3.1 Клиентский центр уведомлений

`frontend/src/layouts/NotificationCenter.vue`:

- Источники — три готовых API, собираются при монтировании и каждые
  60 с (`refresh()`, строки 174–186):
  1. ошибки синхронизаций за 24 ч: `/integrations/sync-jobs` → для
     каждого активного job `/integrations/sync-runs?sync_job_id=…&limit=20`
     (строки 92–128);
  2. бэкапы: `/system/backups` — нет успешного свежее 25 ч либо
     `status=error` за 25 ч (строки 130–153);
  3. непроверенные предложения ИИ: `/ai/proposals?status=pending`
     (строки 155–172).
- Прочитанность — `localStorage['erp-read:' + id]`, id строится как
  `тип:entity_id:дата` (строки 30, 53–72) — см. боли в §1.
- Переходы по клику — `router.push(item.to)` на статические маршруты
  `/integrations/sync`, `/settings/system`, `/assistant` (строки 74–78).
- Примечание к формулировке задания: «просроченных счетов» и «низких
  остатков» среди источников клиентского центра **нет** — фактический
  набор по коду указан выше. Кроме того, «просроченные счета» в модели
  данных не существуют: у транзакций нет срока оплаты (`transactions`
  без due-date, `src/modules/mgmt_accounting/models.py:146–194`), у
  заказов клиентов тоже (`sales_orders`,
  `features/sales/models.py:32–60`). Подробности — О5 §15.

### 3.2 Событийная шина

- `publish()` пишет в `erp_core.event_outbox` в транзакции бизнес-данных
  (`src/core/events.py:39-41`); модель — `src/core/models.py:158-170`.
- Диспетчер `dispatch_outbox()` (`events.py:44-71`): SELECT … FOR UPDATE
  SKIP LOCKED, **помечает `processed=True` и коммитит до вызова
  обработчиков** (строка 62), затем вызывает in-process-подписчиков
  реестра `_handlers`. Вызывается: из точек API после коммитов
  (`src/modules/integrations/router.py:386,436`,
  `src/modules/integrations/sales_flow.py:425,557`,
  `src/modules/mgmt_accounting/features/sales/router.py:482`) и beat-задачей
  каждые 30 с (`src/worker.py:49-52` → `dispatch_outbox_task`,
  `src/modules/integrations/tasks.py:145-152`).
- **Redis pub/sub в шине нет** — упоминание осталось только в докстринге
  `EventOutbox` («Redis pub/sub + локальные обработчики»,
  `src/core/models.py:159-160`). Фактическая доставка — вызов локальных
  обработчиков в процессах api и worker; подписчики регистрируются в
  обоих (`register_event_handlers()` вызывается из
  `src/core/plugins.py:70-88` и из `src/worker.py:18-20` — иначе события
  помечались бы processed без доставки).
- Следствие для уведомлений: сбой обработчика после метки `processed` =
  событие теряется без ретрая. In-app-потребитель проектируется
  идемпотентным и best-effort (как существующий Telegram-обработчик),
  см. §6.1 и О8 §15.

### 3.3 Telegram-уведомления (notification_rules)

- Модель: `integrations.notification_rules`
  (`src/modules/integrations/models.py:329-348`): `company_id NOT NULL`
  (добавлена мультитенантностью, миграция 0030; исходная таблица —
  миграция `migrations/versions/0008_notification_rules.py`), `name`,
  `event_name`, `chat_id`, `template` (подстановка `{ключ}` из payload
  верхнего уровня), `is_active`.
- Обработчики: белый список `NOTIFY_EVENTS` (~20 событий: `acc.*`,
  `crm.*`, `integration.*`, `system.*`;
  `src/modules/integrations/notify.py:24-52`). На каждое событие
  подписка `make_notification_handler` (строки 92–117): выбирает
  активные правила по `event_name` + `company_id` из payload, ставит
  Celery-задачу `notify_task` (`src/modules/integrations/tasks.py:114-127`),
  отправка через коннектор `telegram_bot` (`notify.py:68-89`).
- API правил: `GET/POST/DELETE /api/v1/integrations/notification-rules`,
  `POST …/{id}/test`, право `require_module("integrations")`
  (`src/modules/integrations/router.py:1119-1186`). UI —
  `/integrations/notifications` → `frontend/src/views/NotificationsView.vue`.
- **Находка-противоречие 1**: telegram-connection выбирается глобально —
  «самый свежий активный `telegram_bot`» по всем организациям, без
  фильтра `company_id` (`notify.py:72-76`). В мультитенантной установке
  правило организации X может уйти ботом организации Y.
- **Находка-противоречие 2**: у трёх групп событий payload не содержит
  `company_id`, а обработчик правил матчит правила строго по
  `company_id` из payload (`notify.py:103-107`; «без него — только
  правила без привязки, таких после 0030 нет»):
  - `integration.sync.failed` — публикуется с `{job, error}` без
    `company_id`/`job_id` (`src/modules/integrations/tasks.py:246-249`);
  - `integration.payment.processed` / `.failed` — payload без
    `company_id` (`src/modules/integrations/sales_flow.py:298-302, 417-423`);
  - `ai.proposal.*` — payload `{proposal_id, action_type}` без
    `company_id` и `user_id` (`src/modules/ai_agent/proposals.py:23-28`).
  То есть org-правила по этим событиям фактически не срабатывают.
  Корректные payload (с `company_id`): `acc.inventory.low_stock`
  (`features/inventory/service.py:462-473`), `acc.sales.order.created`
  (`features/sales/service.py:156-168`) и прочие `acc.*`/`crm.*`.

### 3.4 Прочие факты as-is

- **Бизнес-события-кандидаты уже публикуются** (проверено по коду):
  `acc.inventory.low_stock` — при каждом движении, если остаток ≤
  `items.low_stock_threshold` (NULL = не проверять;
  `features/inventory/models.py:51-52,73`; проверки
  `features/inventory/service.py:462-473`, вызовы на строках 518, 646);
  `integration.sync.failed` — финальный провал job после ретраев
  (`tasks.py:246-249`); `integration.payment.processed/failed` —
  онлайн-продажи (sales_flow); `acc.sales.order.created/confirmed`,
  прочие `acc.sales.*`, `crm.*`; `ai.proposal.created`
  (`src/modules/ai_agent/proposals.py:76`).
- **Заявки на регистрацию**: `signup_requests`
  (`src/core/models.py:266-289`); после verify — письмо платформенному
  админу на Setting `platform_notify_email` через платформенный SMTP
  (`src/core/signup.py:117-129`); in-app уведомления нет, события в
  шину не публикуются.
- **Дедлайн 2FA**: `users.totp_setup_deadline` (`src/core/models.py:42`);
  после дедлайна вход блокируется до настройки (мультитенантность,
  этап D). In-app напоминаний нет — только мастер при входе.
- **Просрочки CRM**: сделки `expected_close_at`
  (`src/modules/mini_crm/models.py:65`), задачи `due_at` с фильтром
  «просроченные = `due_before` + `status=open`»
  (`src/modules/mini_crm/models.py:94-106`); у сделки есть
  `responsible_id` — персональная адресация возможна.
- **Бэкапы**: `erp_core.backups` (`src/core/models.py:364-376`) — без
  `company_id` (бэкап — вся установка).
- **Celery/beat**: расписание в `src/worker.py:42-75` (dispatch-outbox
  30 с, sync-jobs каждую минуту, бэкап, verify, check-update); очереди
  `default`/`integrations`; include-список задач — `worker.py:14`.
- **`/auth/me`**: `src/core/router.py:1420-1445` — счётчика непрочитанных
  нет. Пагинация — `PageParams`/`Page` (`src/core/pagination.py`):
  `?limit&offset&format=paginated` → `{items, total}`.
- **Права**: обычная сессия — `HumanUser` (`src/core/auth.py:188`);
  API-токены — отдельный принципал (`ApiPrincipal`). Миграции: последняя
  `0038_maintenance_views.py` → новая — `0039`.
- **i18n**: ключи `notify.*` уже существуют (центр v1-lite).

## 4. To-be: обзор и роли

Уведомление — запись в ядре (`erp_core.notifications`), создаваемая
(а) потребителем событий шины и (б) периодическими проверками (beat).
Центр уведомлений во всех клиентах (веб, позже Tauri) читает один API.
Инварианты:

- **Персональность**: строка всегда имеет конкретного `user_id`
  (аудитория разворачивается в строки при создании — §5.2). Чужие
  строки физически недоступны: все запросы фильтруются по `user_id`
  из сессии — «уведомление чужой организации не видно» выполняется
  автоматически (у другого пользователя другой `user_id`).
- **Идемпотентность генерации**: `dedup_key` + `INSERT … ON CONFLICT DO
  NOTHING` — повтор события или два прогона beat не плодят дублей.
- **Мультитенантность**: `company_id` на строке (NULL = платформенное);
  события без `company_id` в payload адресовать нельзя — такие события
  сначала чинятся (§6.2, этапы A/B).
- **Права**: чтение/прочтение — любой аутентифицированный человек
  (`HumanUser`), без `require_module`: уведомления персональны и не
  дают доступа к данным сверх того, что уже видно получателю (контент
  для широких аудиторий — без сумм, §10). API-токены (`X-API-Token`)
  уведомлений не получают — центр не для служебных учёток.
- **Без новой сетевой активности**: in-app-канал — только запись в БД;
  сеть остаётся в коннекторах (ADR-001, Telegram — как сегодня).
- Модули не трогают чужие схемы: периодика остатков живёт в
  `mgmt_accounting` (своя схема), CRM-просрочки — в `mini_crm`, ядро
  даёт публичный хелпер `notify()` (по образцу `record_version` из
  `src/core/versioning.py`).

**Роли и их уведомления** (v1–v2, реестр типов §6.4):

| Роль | Что получает | Типы |
|---|---|---|
| Админ организации | падения синхронизаций, низкие остатки, онлайн-заказы и их ошибки, новые заказы клиентов, просроченные сделки, состояние бэкапов | `sync_failed`, `low_stock`, `online_order`, `online_payment_failed`, `sales_order_created`, `deal_overdue`, `backup_stale` |
| Сотрудник (лично) | дедлайн настройки 2FA (руководители), просроченные задачи CRM (ответственный), свои предложения ИИ | `totp_deadline`, `crm_task_overdue`, `ai_proposal` |
| Платформенный админ | подтверждённые заявки на подключение, состояние бэкапов установки | `signup_request`, `backup_stale` |

## 5. Модель данных

### 5.1 Таблица `erp_core.notifications`

| колонка | тип | примечание |
|---|---|---|
| `id` | UUID PK | |
| `company_id` | UUID NULL FK `erp_core.companies` | NULL = платформенное (адресаты — платформенные админы); нужно для ретеншна/аналитики, не для изоляции |
| `user_id` | UUID NOT NULL FK `erp_core.users` | единственный адресат строки; вся изоляция — по нему |
| `audience` | String(20) | происхождение адресации: `user` \| `admins` \| `all` \| `platform_admins` (для UI-группировок и будущих настроек) |
| `kind` | String(40), index | тип из реестра §6.4 |
| `severity` | String(10) | `info` \| `warning` \| `critical` (маппинг на red/amber/sky в UI) |
| `title` | String(255) | готовый текст (ru), без i18n-подстановок на клиенте |
| `body` | Text, default "" | детали (без секретов и без сумм для широких аудиторий, §10) |
| `entity_type` | String(100) NULL | строковый ключ модуля, как в `events_log`/`record_versions` (`item`, `sync_job`, `sales_order`, `signup_request`, …) |
| `entity_id` | String(64) NULL | UUID/число сущности для перехода |
| `link` | String(255), default "" | относительный маршрут фронта (`/inventory`, `/integrations/sync`, …) из реестра типов |
| `dedup_key` | String(255) NULL | детерминированный ключ §6.3 |
| `read_at` | DateTime(tz) NULL | NULL = непрочитано |
| `created_at` | DateTime(tz), server_default now() | |

Индексы:

- `UNIQUE (user_id, dedup_key) WHERE dedup_key IS NOT NULL` — схлопывание
  повторов per-получателя;
- `(user_id, created_at DESC)` — список;
- partial `(user_id) WHERE read_at IS NULL` — счётчик непрочитанных;
- `(company_id, created_at DESC)` — ретеншн/аналитика.

### 5.2 Fan-out вместо «общей строки» (решение по модели)

В брифе фигурировал вариант `user_id NULL = роли` + `audience` +
`read_at` на той же строке. Он внутренне противоречив: `read_at` на
общей строке делает уведомление прочитанным для всех после первого
читателя (или требует отдельной таблицы прочтений). Решение:

- адресация выполняется **при создании**: `audience=user` → 1 строка,
  `admins` → по строке каждому активному админу организации (обычно
  1–2), `all` → каждому активному пользователю организации,
  `platform_admins` → каждому `is_platform_admin`;
- «прочитать все» — один `UPDATE … WHERE user_id = me AND read_at IS
  NULL`; счётчик — один `COUNT(*)` по partial-индексу; никаких JOIN.

Альтернатива (одна строка + `notification_reads(user_id,
notification_id)`) отвергнута для v1: дороже чтение, сложнее mark-all и
total. Триггер пересмотра — регулярные аудитории `all` при >50
пользователях в организации (О7 §15).

### 5.3 Миграция `0039_notifications.py`

Обратимая, без изменения существующих таблиц:

1. `op.create_table("notifications", …)` в схеме `erp_core` + индексы
   §5.1 (partial UNIQUE — через `sqlalchemy.Index(..., unique=True,
   postgresql_where=…)`).
2. Downgrade: `op.drop_table("notifications", schema="erp_core")`.

Этап C добавляет миграцию `0040` (колонка `channels` в
`integrations.notification_rules`, §12): `JSONB NOT NULL DEFAULT
'["telegram"]'` — существующие правила не меняют поведения.

## 6. Генерация уведомлений

Два источника: события шины (§6.1) и периодика beat (§6.2). Оба идут
через один хелпер ядра с dedup (§6.3). Реестр типов — §6.4.

### 6.1 Потребитель событий (ядро)

Новый пакет `src/core/notifications/` (слои по ADR-005): `service.py`
(хелпер), `consumers.py` (подписки), `registry.py` (типы), задачи — в
`src/core/tasks.py` (уже в include-списке воркера).

- Подписка — по образцу Telegram-механизма:
  `register_notification_consumers()` вешает обработчик на каждое
  событие из реестра; вызов — из `register_event_handlers()`
  (`src/core/plugins.py:70-88`), чтобы потребитель жил и в api, и в
  worker (иначе диспетчер пометит события processed без доставки — так
  устроена шина, §3.2).
- Обработчик события: открыть `SessionLocal` (паттерн
  `notify.py:96-110`), взять из payload `company_id` (нет — событие
  пропускается с WARNING в журнал: адресовать некому; это же защищает
  от кросс-тенантной утечки), вычислить аудиторию по реестру, вызвать
  `notify()` для каждой строки с dedup_key события. Вставка
  синхронная (запись в БД — не сеть; отличие от Telegram, где нужен
  celery-раундтрип из-за блокирующего httpx).
- Надёжность — best-effort как у шины: диспетчер уже пометил событие
  processed (§3.2); исключение в потребителе логируется и не роняет
  остальных подписчиков (`events.py:65-69`). Потеря единичного
  уведомления не критична (периодика §6.2 подстраховывает остатки и
  дедлайны; событийные типы дублируются каналом Telegram). Ретраи
  доставки — вне v1 (потребуют изменения семантики outbox — отдельное
  решение, О8 §15).

### 6.2 Периодические проверки (beat)

Расширение `beat_schedule` (`src/worker.py:42-75`); все задачи — очередь
`default`, идемпотентны за счёт dedup_key с дневным окном.

| Задача | Расписание | Где живёт | Логика |
|---|---|---|---|
| `sweep_low_stock` | раз в час | `src/modules/mgmt_accounting/features/inventory/tasks.py` (новый; добавить в include `worker.py:14`) | по активным items организации с `low_stock_threshold IS NOT NULL`: `on_hand <= threshold` → `low_stock` админам, dedup `low_stock:{item_id}:{YYYY-MM-DD}` |
| `totp_deadline_reminder` | ежедневно 06:00 | `src/core/notifications/tasks.py` | пользователи с `totp_setup_deadline` через 7/3/1/0 дней и 2FA не включена (`user_totp.enabled_at IS NULL`) → личное `totp_deadline`, dedup `totp:{user_id}:{N}` (N — число дней; каждая ступень — отдельное уведомление) |
| `backup_stale_check` | ежедневно 06:15 | ядро (таблица `erp_core.backups` своя) | нет успешного бэкапа свежее 25 ч либо `status='error'` за 25 ч → `backup_stale` платформенным админам (бэкап — вся установка, `company_id` строки NULL), dedup `backup:{YYYY-MM-DD}` |
| `sweep_crm_overdue` (этап B) | ежедневно 07:00 | `src/modules/mini_crm/tasks.py` (новый) | задачи `status=open, due_at < today` → `crm_task_overdue` ответственному сделки (`deals.responsible_id`), dedup `crm_task:{task_id}:{YYYY-MM-DD}`; сделки `expected_close_at < today` в открытой стадии → `deal_overdue` ответственному, dedup аналогично |

Повторные прогоны задачи в тот же день не создают дублей — UNIQUE
`(user_id, dedup_key)`; «вылечено и снова упало в тот же день»
схлопывается до одного уведомления (осознанная граница v1).

### 6.3 Хелпер и dedup_key

`src/core/notifications/service.py` — публичный контракт ядра для
модулей (как `record_version`):

```python
def notify(db, *, company_id, kind, audience="admins", user_id=None,
           title, body="", entity_type=None, entity_id=None,
           dedup_key=None, severity=None) -> int
```

- резолвит получателей по audience (запросы к `erp_core.users`; только
  активные; `user_id` обязателен при `audience="user"`); severity по
  умолчанию — из реестра;
- для каждого получателя `INSERT … ON CONFLICT (user_id, dedup_key) DO
  NOTHING` (dedup_key NULL → конфликт невозможен, вставка всегда);
- возвращает число созданных строк (для журнала/метрик); не коммитит —
  коммит на вызывающем (в обработчике события/задаче).

Формат dedup_key — `{kind}:{entity_id}[:{bucket}]`, где `bucket` — окно
схлопывания: для событийных типов отсутствует (одно условие — одна
строка навсегда: «платёж X обработан»), для периодики — день
(`YYYY-MM-DD`). Ключ не включает `user_id` (он в UNIQUE-ограничении).

### 6.4 Реестр типов (в коде ядра, не в БД)

| kind | severity | источник | audience | link | entity |
|---|---|---|---|---|---|
| `sync_failed` | critical | событие `integration.sync.failed` | admins | `/integrations/sync` | `sync_job` |
| `low_stock` | warning | событие `acc.inventory.low_stock` + sweep §6.2 | admins | `/inventory` | `item` |
| `online_order` | info | событие `integration.payment.processed` | admins | `/crm/orders` | `sales_order` |
| `online_payment_failed` | critical | событие `integration.payment.failed` | admins | `/integrations/payments` | `online_payment` |
| `sales_order_created` | info | событие `acc.sales.order.created` | admins | `/crm/orders` | `sales_order` |
| `backup_stale` | warning | периодика | platform_admins | `/settings/system` | `backup` |
| `totp_deadline` | warning | периодика | user | `/settings/security` | `user` |
| `signup_request` | info | событие `platform.signup.verified` (новое, публикует `signup.py` — §11) | platform_admins | `/select-org` | `signup_request` |
| `ai_proposal` (этап B) | info | событие `ai.proposal.created` | user (автор) | `/assistant` | `ai_proposal` |
| `crm_task_overdue` (этап B) | warning | периодика | user (responsible) | `/crm/deals` | `crm_task` |
| `deal_overdue` (этап B) | warning | периодика | user (responsible) | `/crm/deals` | `crm_deal` |

Расширение реестра — правка кода с ревью (как `NOTIFY_EVENTS`);
«пользовательские» типы без кода — вне v1 (§14).

### 6.5 Сценарии (Given/When/Then)

Основные:

- G: активный sync-job организации A, все ретраи провалились.
  W: `run_job` публикует `integration.sync.failed` (payload с
  `company_id` после правки §12-A), диспетчер отдаёт событие
  потребителю. T: у каждого админа org A появилась непрочитанная строка
  `sync_failed` c `link=/integrations/sync`; у админа org B и
  сотрудников — нет.
- G: товар с порогом 5, остаток стал 3. W: любое складское движение
  (событие) и/или часовой sweep. T: ровно одна строка `low_stock` на
  админа за день; повторные движения в тот же день не добавляют строк
  (dedup).
- G: руководитель без настроенной 2FA, до дедлайна 3 дня.
  W: ежедневный `totp_deadline_reminder`. T: личное уведомление
  `totp_deadline` («осталось 3 дня»); завтра при том же состоянии —
  нет (dedup `totp:{user}:3` уже есть), на пороге «1 день» — новое.

Альтернативные:

- G: платформенный админ без выбранной организации. W: подтверждена
  заявка на подключение. T: у него (и только у платформенных админов)
  строка `signup_request` с `company_id IS NULL`.
- G: readonly-сотрудник. W: `GET /notifications`. T: 200 — свои
  персональные уведомления (`totp_deadline`, задачи) читаются без прав
  на модули.

Ошибочные:

- G: событие без `company_id` в payload (неисправленный тип).
  W: потребитель получает его. T: WARNING в журнал, строк не создано;
  остальные подписчики (Telegram, рецепты) работают как раньше.
- G: два диспетчера (api и beat) одновременно. W: одно и то же событие.
  T: SKIP LOCKED в `dispatch_outbox` даёт событие одному; даже при
  гонке потребителей UNIQUE `(user_id, dedup_key)` оставит одну строку.

## 7. API

Ядро, префикс `/api/v1` (`src/core/router.py`, рядом с `/auth/me`).
Право — обычная сессия `HumanUser`; у API-токенов (`ApiPrincipal`)
доступа нет (403 — принципал не человек, бейдж ему не нужен).

### 7.1 `GET /notifications`

Свои уведомления, новые сверху. Параметры:

- `limit/offset/format=paginated` — конвенция `PageParams`
  (`src/core/pagination.py`): без параметров — полный массив
  (совместимость), с любым из них — `{items, total}`;
- `unread=true` — только непрочитанные (`read_at IS NULL`);
- `kind=<ключ>` — фильтр по типу (список через запятую).

Ответ — `NotificationOut`:

```json
{
  "id": "uuid", "kind": "sync_failed", "severity": "critical",
  "title": "Синхронизация упала: Ozon-заказы",
  "body": "Ошибка: timeout …",
  "link": "/integrations/sync",
  "entity_type": "sync_job", "entity_id": "17",
  "audience": "admins", "company_id": "uuid|null",
  "read_at": null, "created_at": "2026-10-05T06:15:00+00:00"
}
```

### 7.2 `GET /notifications/unread-count`

`{"count": N}` — по partial-индексу; самый дешёвый эндпоинт для бейджа
и Tauri-поллинга.

### 7.3 `POST /notifications/read`

Тело: `{"ids": ["uuid", …]}` или `{"all": true}`. Обновляет только
свои строки (`WHERE user_id = me`); чужие id молча пропускаются
(не 404 — массовая операция). Ответ: `{"updated": N}`. Идемпотентно:
повтор — `updated: 0`.

### 7.4 Сценарии

- G: пользователь с 3 непрочитанными. W: `GET /notifications?limit=2&
  format=paginated`. T: `{items: [2 шт.], total: 3}`.
- G: прочитано 2 из 3. W: `unread-count`. T: `{"count": 1}`;
  `GET /notifications?unread=true&limit=0` возвращает 1 строку —
  счётчик сходится со списком (критерий приёмки).
- G: попытка `POST /notifications/read` с uuid чужого уведомления.
  W: выполнение. T: `updated: 0`, чужая строка не изменилась.
- G: запрос с `X-API-Token`. W: любой `/notifications*`. T: 403.

## 8. UI (веб)

`NotificationCenter.vue` — единственная точка интеграции; визуал не
меняется (макет §5.1).

- **Источник данных**: `GET /notifications?limit=50` (+ локальный
  фильтр all/unread как сейчас); бейдж — `GET
  /notifications/unread-count`; poll 60 с сохраняется (таймер уже
  есть, строки 181–191).
- **Прочитанность**: `read_at` с сервера; клик по элементу —
  `POST /notifications/read {ids:[id]}` + переход по `link`;
  «Прочитать все» — `{all: true}`. Логика `localStorage`/`erp-read:*`
  и `readTick` удаляются.
- **Клиентские источники v1-lite** (sync-runs, backups, ai-proposals —
  §3.1) выводятся из эксплуатации по мере серверного покрытия:
  `sync_failed` и `backup_stale` — в этапе A (серверные типы уже
  есть), `ai_proposal` — в этапе B (событие есть, payload
  дорабатывается); до этапа B клиентский источник ai-proposals
  остаётся параллельно с серверными строками.
- **Футер «Показать все»**: в A связывается с полноэкранным списком —
  новый маршрут `/notifications` (простой PaginatedList с фильтром
  unread и кнопкой «Прочитать все»); если страница не успевает в A —
  кнопка скрывается, маршрут обязателен в B (Tauri открывает его из
  тоста).
- **SSE — оценка**: мгновенность упирается не в клиент, а в
  долгоживущие соединения через nginx (`proxy_buffering off`,
  таймауты) и однопроцессный api. Для коробки МСБ poll 60 с (Tauri —
  30 с, настраивается клиентом) закрывает потребность; SSE/WebSocket —
  v2 вместе с Tauri v1.1, если пилоты попросят «мгновенно» (О4 §15).
  Рекомендация: v1 — poll.

## 9. Tauri-контракт (оболочка v1.1)

Параллельная спека Tauri-оболочки (в работе; видение — решения реестра
2026-09-19, `docs/design/box-installer-spec.md`: Tauri — v1.1) получает
готовый контракт без доработок сервера:

- **Поллинг нативных тостов**: `GET /notifications/unread-count` каждые
  N секунд (рекомендация 30 с) → при росте счётчика `GET
  /notifications?unread=true&limit=20` → тост из `title`/`body`,
  критичность из `severity`, клик — открыть окно на `link` (маршруты
  те же, что в вебе).
- Требования к контракту (фиксируются в этапах A/B, ломать нельзя —
  ADR-002): поля `NotificationOut` §7.1 стабильны; авторизация — тот
  же Bearer (оболочка уже умеет); никаких cookies/CSRF.
- Ограничение: персональная адресация означает «тосты только для
  вошедшего пользователя оболочки» — broadcast-рассылки вне модели
  (свойство модели, не баг).

## 10. Безопасность

- **Изоляция**: все запросы — `WHERE user_id = <из сессии>`; прямого
  доступа к чужим строкам нет ни списком, ни id (mark-read чужих —
  no-op). Платформенные строки (`company_id NULL`) видит только их
  адресат.
- **Контент**: title/body создаются кодом ядра из реестра —
  пользовательский ввод туда не попадает (кроме названий job/sku из
  payload — без секретов). Широким аудиториям (`all`) — без сумм и
  ПДн; суммы допустимы только админским типам. Тексты уведомлений не
  раскрывают больше, чем получатель уже видит своими правами.
- **Чтение не журналируется** — консистентно с решением владельца по
  devtools (О4/О6, 2026-10-04): аудит — только изменения; из
  уведомительных мутаций только `read`, и её не журналируем (шум).
- **Спам**: dedup_key §6.3 + отсутствие пользовательской настройки
  «уведомлять о чём угодно» в v1 (реестр в коде).
- **Неактивные не получают**: резолвер аудитории берёт только
  активных пользователей (`is_active`).

## 11. Влияние

- **Ядро**: `src/core/models.py` (+Notification), новый пакет
  `src/core/notifications/{service,consumers,registry,tasks}.py`;
  `src/core/router.py` (+3 эндпоинта); `src/core/plugins.py`
  (регистрация потребителя в `register_event_handlers`);
  `src/worker.py` (beat-записи, include задач). Миграция 0039.
- **integrations**: правка payload — `company_id` (+ `job_id`) в
  `integration.sync.failed` (`tasks.py:246-249`), `company_id` в
  `integration.payment.processed/failed` (`sales_flow.py:298-302,
  417-423`); этап C — колонка `channels` в `notification_rules`
  (миграция 0040) + фильтр connection по company в `notify.py:72-76`
  (находка §3.3-1). Событие `platform.signup.verified` публикует
  `src/core/signup.py` (verify → publish + существующее письмо).
- **mgmt_accounting**: `features/inventory/tasks.py` (sweep low_stock);
  событие `acc.inventory.low_stock` уже публикуется с `company_id` —
  обработчик события добавляется в ядро (этап A), sweep дублирует его
  как страховку «когда вкладки закрыты».
- **mini_crm** (этап B): `tasks.py` (sweep просрочек), без изменений
  моделей.
- **ai_agent** (этап B): payload `ai.proposal.created` + `user_id`,
  `company_id` (`proposals.py:23-28`).
- **Шина**: новых событий кроме `platform.signup.verified` нет;
  потребители подписываются стандартно. Схема outbox не меняется.
- **RBAC**: без изменений (новых ключей модулей нет — персональные
  данные, не полномочия).
- **Фронтенд**: `NotificationCenter.vue` (источник/бейдж/read),
  новый `/notifications` (полный список), i18n-ключи `notify.*`
  (часть уйдёт — тексты с сервера; добавить для нового экрана);
  `frontend/src/api/notifications.ts` (типизированный клиент). Правило
  реестра: коммит с `frontend/**` → пересборка web.
- **Тесты**: `tests/test_notifications.py` (dedup, изоляция, счётчик,
  read, аудитории, payload-гэпы) + smoke-секция «notifications»
  (создать условие → увидеть → прочитать → счётчик 0).

## 12. Этапы, объём

### Этап A — модель + API + периодика + UI (~1 неделя)

Модель/миграция 0039; хелпер `notify()` с dedup; API §7 (3 эндпоинта);
потребитель-пилот на `integration.sync.failed` (+правка payload
`company_id`/`job_id`); периодика: `sweep_low_stock`,
`totp_deadline_reminder`, `backup_stale_check`; UI:
`NotificationCenter.vue` на сервер (бейдж, read, poll 60 с), клиентские
источники sync/backup удалены, ai-proposals остаётся клиентским до B.
Бэкенд 3–4 дня, фронт 1–2, тесты/полировка 1–2.

### Этап B — события + dedup-нагрузка + Tauri-контракт (~1 неделя)

Потребители: `online_order`/`online_payment_failed` (правка payload
`company_id`), `sales_order_created`, `ai_proposal` (payload `user_id`;
удаление клиентского источника), `signup_request` (событие
`platform.signup.verified` в `signup.py`); периодика `sweep_crm_overdue`
(`crm_task_overdue`, `deal_overdue`); полноэкранный `/notifications`;
фиксация Tauri-контракта (§9) в параллельной спеке; нагрузочный прогон
генератором демо-данных (`scripts/generate_demo_month.py`) — нет
дублей, счётчики сходятся. Бэкенд 3–4 дня, фронт 1–2, тесты 1–2.

### Этап C — единая точка «условие → каналы» (~3–4 дня)

Колонка `channels` (`["telegram"]` дефолт; `["in_app","telegram"]` —
оба) в `notification_rules` (миграция 0040); семантика: активное
правило по событию с `channels` без `in_app` глушит in-app-создание
для пары (org × event) — точка выключения дублирования без второй
таблицы; UI правил (`NotificationsView.vue`) — чекбоксы каналов и
подсказка «in-app-типы работают и без правил»; мьют типов организации —
Setting `notifications.muted_kinds` (JSONB, список kind): потребитель
и sweep пропускают замьюченные; фикс telegram-connection по company
(`notify.py:72-76`). Попутно закрыть О1 (ретеншн), если решён.

## 13. Критерии приёмки (чек-лист QA)

Общие (этап A):

1. **Персистентность**: прочитать уведомление → перезагрузить страницу
   (и войти с другого браузера) — прочитанность на месте (`read_at` на
   сервере); в localStorage нет ключей `erp-read:*`.
2. **Дедуп периодики**: два подряд ручных запуска `sweep_low_stock`
   (или часовое ожидание) при том же условии — в БД одна строка
   `low_stock` на админа (проверка по `(user_id, dedup_key)`).
3. **Дедуп событий**: повторная публикация того же события — строк не
   добавляется.
4. **Изоляция**: пользователь org B: `GET /notifications` не содержит
   строк org A; `total` совпадает со срезом БД по `user_id`; попытка
   mark-read чужого id — `updated: 0`.
5. **Счётчик сходится**: `unread-count` == число строк
   `GET /notifications?unread=true&limit=0`; после «Прочитать все» —
   0 у себя, у остальных адресатов их строки не тронуты.
6. **Права**: readonly-сотрудник — 200 на все `/notifications*`;
   `X-API-Token` — 403.
7. **sync_failed живьём**: падающий sync-job (кривой endpoint) → у
   админов организации строка с `link=/integrations/sync`; payload
   события содержит `company_id` (проверка в `event_outbox`).
8. **totp_deadline**: пользователь с дедлайном через 3 дня без
   включённой 2FA → личное уведомление после задачи; с включённой 2FA —
   нет.
9. **backup_stale**: (тест-хук/симуляция старого бэкапа) → уведомление
   платформенным админам, `company_id IS NULL` в строке.
10. **UI**: бейдж = счётчику; клик по элементу читает и переходит по
    `link`; фильтр «Непрочитанные» работает; poll подхватывает новое
    уведомление без перезагрузки (в пределах 60 с).
11. `pytest` + `ruff` зелёные; smoke-секция notifications проходит;
    миграция 0039 накатывается и откатывается на тестовой БД (на живой
    downgrade запрещён — ADR-004).

Этап B дополнительно:

12. Онлайн-платёж (мок ЮKassa из smoke 24f) → `online_order` админам с
    ссылкой на заказ; `manual`-платёж → `online_payment_failed`.
13. `signup verify` (тест) → `signup_request` у платформенных админов,
    `company_id IS NULL`.
14. Просроченная CRM-задача (due_at вчера, open) после sweep — личное
    уведомление ответственному; выполненная задача — нет.
15. Прогон генератора демо-данных + сутки beat: отсутствие дублей по
    всем kind (`SELECT kind, user_id, dedup_key … HAVING count(*) > 1`
    → пусто).
16. Tauri-контракт: поля §7.1 стабильны, `unread-count` отвечает
    быстро (<50 мс на свежей БД).

Этап C дополнительно:

17. Правило с `channels=["telegram"]` на событие с in-app-типом —
    строки in-app не создаются для этой org; `["in_app","telegram"]` —
    оба канала; без правил — in-app работает (умолчание).
18. `notifications.muted_kinds=["low_stock"]` — sweep и событие не
    создают строк; Telegram-правила продолжают работать.
19. Telegram-connection подбирается в пределах организации (две org с
    разными connections — каждая шлёт своим ботом).

## 14. Что НЕ входит в эту версию

- **SSE/WebSocket** — poll (О4); **push-мобильные** — нет.
- **Email-канал** — вне v1 (О3): уведомления не письма; письма остаются
  в своих сценариях (signup, восстановление пароля).
- **Пользовательская настройка правил in-app произвольного вида**
  («уведомлять при сумме > X») — реестр типов в коде; настройка
  ограничена мьютом типов (этап C).
- **«Просроченные счета»** — в модели нет срока оплаты транзакций и
  заказов (§3.1); дебиторка видна отчётами. Введение
  `payment_due_date` и типа `invoice_overdue` — отдельная доменная
  задача (О5).
- **Ретраи доставки событий** (изменение семантики outbox «processed до
  обработчиков») — отдельное решение по шине, не по уведомлениям (О8).
- **Локализация уведомлений не-ru** — тексты в БД русским (проект
  ru-first, как письма `signup.py`); i18n уведомлений — с релизом EN.
- **Ретеншн/автоочистка** — этап C/О1 (до того — ручной SQL).
- **Аудитория по гранулярным ролям** («кладовщик без прав админа») —
  v2 с профилями делегирования (О6).

## 15. Открытые вопросы (с рекомендациями)

- **О1. Ретеншн/очистка старых.** Рост при периодике — десятки
  строк/день на организацию; без чистки через год ~10–50 тыс. строк на
  org. Рекомендация: beat-задача в C — удалять прочитанные старше 90
  дней (env `NOTIFICATIONS_RETENTION_DAYS`, 0 = не чистить),
  непрочитанные не трогать. Решение — на ревью архитектурного чата.
- **О2. Mute per-type.** Рекомендация: этап C — Setting
  `notifications.muted_kinds` на организацию (админ); личных мьютов в
  v1 нет (усложнение модели настроек) — кандидат v2.
- **О3. Email-канал уведомлений.** Спрос пилотов неизвестен; SMTP-
  коннекторы per-org уже есть (`smtp` в каталоге коннекторов).
  Рекомендация: v2, по требованию; канал объявлен в архитектуре (§2)
  заранее.
- **О4. Мгновенность vs период.** v1: poll 60 с (веб) / 30 с (Tauri);
  событийные in-app создаются при диспетчеризации (≤30 с от факта —
  beat `dispatch-outbox`), периодика — по расписанию. SSE — v2 при
  жалобах на скорость (оценка §8). Рекомендация: принять v1-poll.
- **О5. «Просроченные счета» из брифа.** В модели нет срока оплаты: у
  транзакций и заказов клиентов отсутствует due-поле (§3.1). Варианты:
  (а) ввести `payment_due_date` на `sales_orders` (миграция + поле в
  UI) и тип `invoice_overdue` — отдельная небольшая спека учёта;
  (б) считать «просрочкой» дебиторку из отчёта `counterparty-balance`
  (нет временнОй оси — не считаемо); (в) не делать. Рекомендация: (а)
  после пилотов; в этой версии CRM-просрочки сделок/задач закрывают
  «напоминательную» потребность.
- **О6. Аудитории шире админов** (low_stock кладовщику, online_order
  менеджеру продаж). Требует связки «тип → роль/право», которой в
  rw/ro-матрице нет. Рекомендация: v1 — admins; расширить при профиле
  «склад» в делегировании ролей.
- **О7. Fan-out vs reads-таблица.** Fan-out выбран из простоты (§5.2);
  при аудиториях `all` в организациях >50 человек — пересмотреть.
  Рекомендация: держать fan-out до реального кейса.
- **О8. Диспетчер помечает processed до обработчиков** (`events.py:62`)
  — общее свойство шины: сбой потребителя = потеря конкретного
  уведомления (Telegram живёт с тем же). Менять порядок (process
  после успеха + ретраи) — отдельное решение по шине с нагрузочными
  импликациями (ядовитые события). Рекомендация: не трогать в этой
  спеке; дублирующая периодика снижает цену потери.

---

### Приложение: находки as-is, которые чинятся этой спекой (сводка)

1. `integration.sync.failed`, `integration.payment.processed/failed`
   публикуются без `company_id` → org-scoped Telegram-правила по ним не
   срабатывают (`notify.py:103-107`) и in-app не адресуемо — правка
   payload в этапах A/B (§11).
2. `ai.proposal.*` без `user_id`/`company_id` (`proposals.py:23-28`) —
   правка в этапе B.
3. Telegram-connection выбирается глобально без company-фильтра
   (`notify.py:72-76`) — фикс в этапе C.
4. Докстринг `EventOutbox` обещает «Redis pub/sub»
   (`src/core/models.py:159-160`), которого нет в коде, — учитывать при
   чтении спек/кода (здесь шина описана по факту: outbox + in-process
   диспетчер).
