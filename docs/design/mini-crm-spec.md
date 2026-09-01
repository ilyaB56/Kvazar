# Спецификация модуля mini_crm (v1)

Задание для разработки. Основано на ADR-001/002/003/005. Три этапа,
коммит на каждый. UI — non-goal v1 (экраны пойдут блоком с экранами
учёта).

## Этап A — воронка и сделки

- Пакет `src/modules/mgmt_crm/`... имя пакета: `src/modules/mini_crm/`,
  url_prefix `crm`, схема БД `mini_crm`, depends_on core.
- Миграция: таблицы схемы `mini_crm`:
  - `stages` — справочник воронки: id, name, position, probability
    (int 0–100, nullable), is_won bool, is_lost bool, is_active.
    UNIQUE(position). Сид: Новая(10), Квалификация(30), Предложение(50),
    Согласование(70), Выиграна(100, won), Проиграна(0, lost).
  - `deals`: id, title, stage_id (FK stages), counterparty_id UUID
    nullable (контрагент mgmt_accounting — **без FK**, имя тянется
    публичным API `/api/v1/accounting/counterparties/{id}`, кэш на запрос),
    contact_id UUID nullable (erp_core.contacts, FK на ядро допустим),
    responsible_id UUID nullable (erp_core.users), amount NUMERIC(20,4),
    currency, rate NUMERIC(18,8), amount_base NUMERIC(20,4) — пересчёт и
    заморозка при создании/изменении amount|currency (курс из rates на
    сегодня; нет курса — 422 с подсказкой, как в учёте),
    expected_close_at DATE nullable, lost_reason text (для is_lost),
    dimensions JSONB, is_deleted bool (метка, как в учёте),
    created_by, created_at, updated_at.
  - Индекс GIN pg_trgm по title (extension создать, если нет), поиск
    `GET /api/v1/crm/deals?q=`.
- API (user+; admin — управление стадиями):
  `GET/POST /api/v1/crm/deals`, `GET/PATCH /api/v1/crm/deals/{id}`,
  `POST /api/v1/crm/deals/{id}/move` {stage_id} (в won/lost — из открытых
  стадий; выход из won/lost только в открытую — реанимация сделки),
  `POST /api/v1/crm/deals/{id}/delete-mark` (admin, метка как в учёте),
  `GET/POST /api/v1/crm/stages` (POST/PATCH/DELETE — admin; удаление
  только пустой стадии), `GET /api/v1/crm/deals/{id}/counterparty` —
    прокси-имя контрагента (или поле в выдаче сделки).
- Журнал: все изменения сделки — `record_version` (core.versioning),
  entity_type `crm.deal`.
- **События-контракты** (деньги строками, поля неприкосновенны):
  - `crm.deal.created` {deal_id, title, stage, amount, currency,
    amount_base, responsible_id}
  - `crm.deal.stage_changed` {deal_id, title, from_stage, to_stage,
    amount, currency, amount_base, is_won, is_lost}
  - `crm.deal.won` {deal_id, title, amount, currency, amount_base,
    counterparty_id}
  - `crm.deal.lost` {deal_id, title, reason}
- Правка в integrations (малая): расширить белый список событий
  Telegram-правилами на `crm.*` из этого списка.

**Приёмка A**: сделка 100000 RUB → создана + `crm.deal.created` в outbox;
перемещение по стадиям → `stage_changed`; move в «Выиграна» → `crm.deal.won`
и запрет move из won в lost (422), реанимация в открытую — ок; валютная
сделка 1000 USD → amount_base по курсу ЦБ (half-up до копеек); поиск `q=`
находит по части названия; версии в `/history/crm.deal/{id}`; readonly →
403 на POST.

## Этап B — коммуникации и задачи

- Таблицы: `communications` (id, deal_id FK, kind
  `call|email|meeting|note|other`, content text, occurred_at DATE,
  created_by, created_at), `activities` (id, deal_id FK, title, due_at
  DATE, done bool, done_at, created_by, created_at).
- API (user+): `GET/POST /api/v1/crm/deals/{id}/communications`,
  `GET/POST /api/v1/crm/deals/{id}/activities`,
  `PATCH /api/v1/crm/activities/{id}` (done, title, due_at),
  `GET /api/v1/crm/activities?due_before=&status=` — общий список задач
  (мои + всех для админа; фильтр responsible).
- Событие: `crm.activity.created` {activity_id, deal_id, title, due_at}.

**Приёмка B**: коммуникации и задачи на сделку; отметка done; список
«просроченные» (`due_before=сегодня, status=open`) корректен; событие в
outbox; Telegram-правило на `crm.deal.won` срабатывает (мок-сервер).

## Этап C — отчёт по воронке

- `GET /api/v1/crm/report/pipeline?responsible_id=`: по стадиям
  (count, total, weighted = Σ amount_base × probability/100), итоги,
  выиграно/проиграно за период (`?date_from&date_to` — по дате
  stage_changed в won/lost — хранить won_at/lost_at в deals при move),
  всё в базовой валюте, деньги строками.
- `won_at/lost_at` — миграция в этом же этапе.

**Приёмка C**: три сделки на разных стадиях → pipeline: суммы и
weighted сходятся вручную; won-сделка попадает в «выиграно за период»;
после реанимации — не попадает.

## Общие требования

- Регресс: ruff, pytest (unit: пересчёт amount_base при правке, move-
  правила, weighted-математика; integration против API), smoke (+секции
  crm A/B/C). Структура модуля по ADR-005, события — контракты ADR-002,
  деньги ADR-003, сети нет (ADR-001).
- Non-goals v1: UI, мультиворонки, интеграции почты/телефонии, импорт
  из Excel, автоназначение ответственных, SLA/напоминания по времени
  (просроченные — фильтром).
