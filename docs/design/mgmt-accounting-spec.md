# Спецификация модуля mgmt_accounting (v1)

Модуль управленческого учёта. Задание для разработки. Основано на:
ADR-001 (сеть), ADR-002 (API/`/api/v1`, контракты), ADR-003 (деньги,
мультивалютность), рабочих решениях (mgmt-accounting-notes.md).

## Область v1 (минимум-фундамент)

Счета, статьи, контрагенты, транзакции (поступление/списание/перевод) с
полным жизненным циклом (черновик → проведение → сторно), периоды, курсы
валют, один отчёт «движение денег», журнал версий. Всё остальное
(бюджеты, согласования, разрезы-словари, ИИ) — надстройки позже.

## Схема БД: `mgmt_accounting` (+ одна таблица в `erp_core`)

Общие правила: суммы `NUMERIC(20,4)`, курсы `NUMERIC(18,8)`, никаких float;
`amount_base` округляется до 2 знаков, half-up; миграции обратимые.

### accounts — счета/кошельки
id, company_id (erp_core.companies), name, currency (ISO 4217),
is_active, created_at. Остатки НЕ хранятся — считаются по транзакциям
(кэш — позже, если понадобится).

### categories — статьи (иерархия)
id, parent_id (nullable, self FK), name, kind ('income'|'expense'|'transfer'),
is_active. Переводы категорию не требуют.

### counterparties — контрагенты
id, internal_code (автономер, уникален), name, inn, kpp, contact_id
(nullable → erp_core.contacts), is_active. Дубль по ИНН+КПП — предупреждение
в ответе API (не запрет).

### doc_types — виды документов (данные, не код)
code (ПК/СК/ПР), title, number_prefix, is_active. Сид: ПК «Поступление»,
СК «Списание», ПР «Перевод». Новые виды добавляются строкой справочника.

### doc_sequences — нумерация
(doc_type_code, year, last_number), UNIQUE(code, year). Выдача номера:
`SELECT ... FOR UPDATE` в транзакции проведения. Формат `{prefix}-{ГГГГ}-{%05d}`,
номер присваивается только при проведении, дыр нет.

### rates — курсы к базовой валюте компании
date, currency, rate NUMERIC(18,8), source ('manual'|'cbr'|'connector'),
UNIQUE(date, currency). Наполнение: ручное через API и/или коннектор ЦБ РФ
(через интеграционную платформу, отдельная задача).

### periods — периоды
year, month, status ('open'|'closed'), closed_by, closed_at, UNIQUE(year,month).
Строка месяца создаётся лениво при первой операции в нём.

### transactions — транзакции/документы
- id, doc_number (nullable до проведения), doc_type_code
- kind ('income'|'expense'|'transfer'), status ('draft'|'posted')
- operated_at (DATE — дата операции, вводит пользователь), created_at (UTC)
- amount NUMERIC(20,4), currency, rate NUMERIC(18,8), amount_base NUMERIC(20,4)
  — сторона списания/основная; для перевода дополнительно:
  amount_to, currency_to, rate_to, amount_to_base (сторона зачисления)
- account_id (для transfer — счёт списания), account_to_id (nullable,
  только transfer), category_id (nullable для transfer),
  counterparty_id (nullable), description
- dimensions JSONB DEFAULT '{}' — гибкие аналитические разрезы на будущее
- is_deleted BOOL DEFAULT false (метка удаления, только для дублей)
- storno_of_id (nullable FK transactions) — у сторно-документа
- created_by (erp_core.users)

Суммы хранятся положительными; направление в отчётах определяется kind.

### erp_core.record_versions — журнал версий (generic-таблица ядра)
entity_type (например 'acc.transaction'), entity_id, changed_by, changed_at,
diff JSONB ({поле: {old, new}}), reason (nullable). Пишется сервисным слоем
через общий хелпер ядра (core), чтобы потом переиспользовать в CRM и др.

## Жизненный цикл документа

1. **Черновик**: создаётся без номера, правится и удаляется свободно
   (удаление черновика — единственное физическое удаление).
2. **Проведение** (POST /{id}/post): проверка периода (operated_at не в
   закрытом), расчёт/захват курса (rates на operated_at; нет курса — ошибка
   422 с подсказкой), расчёт amount_base (half-up до 2 знаков), присвоение
   номера из doc_sequences, status='posted', публикация события.
3. **Правка проведённого**: только пока период открыт; PATCH пишет версию
   в record_versions; course/amount_base пересчитываются заново.
4. **Сторно** (POST /{id}/storno {reason}): создаёт парный документ
   (вид «СТ-...», kind инвертирован: income↔expense, для transfer счета
   меняются местами), amount/курсы копируются, storno_of_id указывает на
   исходный, проводится сразу. Пара гасится в отчётах естественным
   суммированием. Исходному ставится is_stornoed=true.
5. **Метка удаления** (POST /{id}/delete-mark {reason}): только admin,
   только для дублей; скрывает из отчётов, остаётся в БД, журналируется.

Полного удаления проведённых документов не существует.

## События-контракты (имена и поля неприкосновенны, ADR-002)

Все денежные величины в payload — строки.

- `acc.transaction.posted`: {transaction_id, doc_number, kind, amount,
  currency, amount_base, rate, operated_at, account_id, category_id,
  counterparty_id, dimensions}
- `acc.transaction.stornoed`: {transaction_id, storno_of, reason}
- `acc.period.closed` / `acc.period.reopened`: {year, month}

## API `/api/v1/accounting/...` (весь — под авторизацией)

- `GET/POST /accounts`, `PATCH /accounts/{id}` (архив — is_active)
- `GET/POST /categories` (иерархия одним списком с parent_id)
- `GET/POST /counterparties`; ответ POST содержит предупреждение о дубле
  ИНН+КПП (`warning`, не ошибку)
- `GET /transactions?date_from&date_to&account_id&category_id&status&kind`
- `POST /transactions` (создаёт черновик; тело содержит kind, operated_at,
  amount, currency, account_id/to, category_id, counterparty_id, description,
  dimensions; флаг `post_immediately` — создать и провести одной командой)
- `PATCH /transactions/{id}` (черновик — свободно; проведённый — с версионной
  записью и только в открытом периоде)
- `POST /transactions/{id}/post` · `POST /transactions/{id}/storno` (admin/user)
  · `POST /transactions/{id}/delete-mark` и `/delete-unmark` (admin)
- `GET /report/cashflow?date_from&date_to[&account_id]` — opening_balance,
  totals по категориям (kind, сумма в базовой валюте), closing_balance
- `GET /rates?currency&date_from&date_to` · `POST /rates` (admin, upsert
  по дате+валюте, source='manual')
- `GET /periods` · `POST /periods/{year}/{month}/close` (admin) ·
  `POST /periods/{year}/{month}/reopen` (admin)
- `GET /history/{entity_type}/{entity_id}` — список версий записи

## Сквозная цепочка с интеграциями (границы модулей)

Интеграционная платформа (recipe с action типа «вызов API») создаёт
транзакции через **публичный API** `/api/v1/accounting/transactions`
(post_immediately=true) от служебной учётки. Модуль учёта НЕ подписывается
на события интеграций и не знает о них — развязка через API, как в правилах.
(Служебная системная роль/токен для рецептов — общесистемная задача,
координируется с разработчиком интеграционного модуля.)

## Требования к реализации

1. Пакет `src/modules/mgmt_accounting/` с manifest.py (depends_on core;
   схема БД mgmt_accounting; регистрация в MANIFESTS).
2. Миграция Alembic (schema mgmt_accounting + erp_core.record_versions),
   downgrade обязателен; сид doc_types и одного рублёвого счёта «Касса».
3. Сервисный слой отделён от роутеров; версии пишет сервисный слой.
4. Тесты: unit — нумерация (параллельность, FOR UPDATE), округление half-up,
   кросс-валютный перевод, сторно-гашение в отчёте, запрет периода;
   integration/smoke — полный цикл из раздела «Приёмка».
5. Лимтер/сеть: сетевых импортов нет вообще (ADR-001); API под `/api/v1`.
6. Валюта по умолчанию для новых сущностей — базовая валюта компании (RUB),
   но поле currency обязательно всегда.

## Приёмка (smoke-чеки для tests/smoke.py)

1. Создать счёт, статью, контрагента (контрагент с ИНН → 201 + warning ok).
2. Черновик поступления 1000 RUB → post → doc_number вида `ПК-2026-00001`,
   событие в outbox (`acc.transaction.posted`).
3. Отчёт cashflow за день: сумма +1000, closing_balance корректен.
4. Кросс-валютный тест: курс USD вручную (90.5555), поступление 100 USD →
   amount_base = 9055.55 (half-up до копеек).
5. Сторно → отчёт net 0, оба документа в списке, is_stornoed у исходного.
6. Закрыть месяц операции → post новой транзакции в нём → 422; reopen → post ок.
7. Метка удаления дубля (admin) → из отчёта исчез, из списка с флагом есть.
8. `GET /history/acc.transaction/{id}` после правки проведённого — версия есть.

## Non-goals v1 (осознанно позже)

Бюджеты, план-факт, согласования, подотчётные лица, регулярные платежи,
словари разрезов (dimensions уже готовы принять), переоценка валют (ур. 2),
ИИ-разнесение, печатные формы.
