# Спецификация: витринная цепочка (ЦБ РФ + Telegram + 1С + токены)

Задание для разработки, пять этапов с коммитом на каждый. Основано на
ADR-001/002/003/005. Порядок: A токены → B cron-планировщик → C ЦБ РФ →
D Telegram → E экспорт 1С. (Этап F «мини-исполнитель рецептов» — опционален,
можно отложить.)

## Этап A — служебные API-токены (core)

Для машинных вызовов API (рецепты, будущий мост, внешний шлюз) — отдельная
механика, не пользовательские JWT.

- Миграция 0006: `erp_core.api_tokens` (id, name, token_hash sha256-hex,
  role DEFAULT 'user', is_active, created_at, last_used_at).
- Аутентификация: зависимость принимает **либо** `Authorization: Bearer JWT`
  (как сейчас), **либо** заголовок `X-API-Token` (sha256 сравнивается с
  token_hash; неактивный/неизвестный → 401; проставлять last_used_at).
- Admin-API: `GET/POST/DELETE /api/v1/admin/api-tokens`; при создании токен
  генерируется и показывается **один раз** (как у webhook); удаление = отзыв
  (is_active=false, токен в audit `api_token.revoked`).
- Права: токен имеет роль (по умолчанию user); ролевые проверки работают
  как для пользователей.

**Приёмка A**: создать токен → `POST /api/v1/accounting/transactions`
с `X-API-Token` (post_immediately) → 201; отозвать → 401; в events_log
есть `api_token.created`.

## Этап B — cron-планировщик sync jobs (integrations)

Поле `cron` у sync jobs есть, исполнителя по расписанию нет.

- Миграция: `integrations.sync_jobs.last_run_at` (timestamp, nullable).
- Beat-задача раз в минуту `run_due_sync_jobs`: активные задания с непустым
  cron, где `croniter(cron, last_run_at or created_at).get_next(<now)`
  уже наступило → `run_job.delay()` + обновить last_run_at. Гонки не
  допускать (обновление last_run_at условием WHERE last_run_at IS NULL OR
  last_run_at < now - интервал).
- Один и тот же job не ставится в очередь повторно, пока не завершился
  прошлый запуск (флаг in_progress в Redis с TTL).

**Приёмка B**: job с `* * * * *` выполняется в течение ~70 с; с `0 3 * * *`
— не выполняется вне времени; параллельных дублей нет.

## Этап C — коннектор ЦБ РФ + автозаполнение rates

- Коннектор `src/modules/integrations/connectors/cbr.py`, code=`cbr`,
  fetch-only. Источник: `GET {base_url}/scripts/XML_daily.asp?date_req=DD/MM/YYYY`
  (base_url в config, дефолт `https://www.cbr.ru`). Парсинг XML
  (`xml.etree`): Valute → CharCode, Nominal, Value (запятая-десятичная);
  **курс = Value / Nominal** — деление на номинал обязательно (₸, ₩ и др.
  котируются за 10/100/1000 единиц). Результат fetch: `{date: ISO, rates:
  [{currency, rate}]}`, деньги — строки (ADR-003).
- Миграция: `integrations.sync_jobs.emit_event` (varchar, по умолчанию
  `integration.data.fetched`). `run_job` публикует указанное событие вместо
  дефолтного (generic-механизм: спец-событие у задания).
- **Контракт события** `integration.rates.fetched`: `{date: 'YYYY-MM-DD',
  source: 'cbr', rates: [{currency: 'USD', rate: '89.1234'}, ...]}` —
  поля неприкосновенны (ADR-002).
- Подписчик в учёте (`manifest.event_handlers`): upsert в
  `mgmt_accounting.rates` (source='connector'), свою сессию создаёт сам.
  Внеурочные/дубли по дате — идемпотентно перезаписываются.
- Сид (в `seed.py`, идемпотентно): connection «ЦБ РФ» (cbr, без секретов) +
  sync job «Курсы ЦБ» cron `30 0 * * *`, emit_event=`integration.rates.fetched`.

**Приёмка C**: ручной запуск джобы → в rates появились USD/EUR/CNY на
сегодня с source='connector'; транзакция в USD проводится **без ручного
ввода курса**; событие `integration.rates.fetched` в outbox; повторный
запуск не дублирует строки.

## Этап D — Telegram-уведомления

- Коннектор `telegram_bot` (push-only): config `api_base` (дефолт
  `https://api.telegram.org`, в тестах подменяется мок-сервером),
  credentials `bot_token`; push → `POST {api_base}/bot{token}/sendMessage`
  `{chat_id, text}`. Сетевой код — только внутри коннектора (ADR-001).
- Миграция: `integrations.notification_rules` (id, name, event_name,
  chat_id, template, is_active).
- Обработчик в манифесте интеграций на события из белого списка:
  `acc.transaction.posted`, `acc.period.closed`,
  `integration.sync.failed` (новое событие — публиковать в run_job при
  финальном провале, payload: {job, error}), `system.updated`,
  `system.rollback`. Обработчик: активные правила по событию → рендер
  шаблона (подстановка `{ключ}` из payload, только простые ключи верхнего
  уровня) → push через коннектор connection «Telegram» (создаётся админом
  с bot_token). Ошибка отправки — в журнал, не валит обработку остальных.
- Admin-API + мини-UI: раздел «Уведомления» — список правил, создание
  (событие из списка, chat_id, шаблон с подсказкой ключей), «Тест»
  (отправка Hello по правилу).

**Приёмка D**: правило на `acc.transaction.posted` → проведение транзакции
→ мок-сервер Telegram получил сообщение с подставленными полями; правило
на `integration.sync.failed` + падающая джоба → сообщение с текстом ошибки;
ошибка сети не ломает диспетчер.

## Этап E — выгрузка первички в 1С

Формат: **1CClientBankExchange** (обмен «клиент-банк», 1С:Бухгалтерия
загружает штатно как выписку).

- Миграция: `mgmt_accounting.accounts.account_number` (varchar, nullable)
  — «РасчСчет» файла; PATCH /accounts уже позволяет правку.
- `GET /api/v1/accounting/export/client-bank?date_from&date_to&account_id`:
  только posted, не is_deleted, не сторно и не is_stornoed, kind ≠ transfer,
  счёт рублёвый и с заполненным account_number (иначе 422 с подсказкой).
  Ответ: `text/plain; charset=windows-1251`, заголовок Content-Disposition
  с именем файла. Секции: шапка (ВерсияФормата=1.02, ДатаНачала/ДатаКонца
  ДД.ММ.ГГГГ, РасчСчет), `СекцияДокумент=Платежное поручение` на транзакцию
  (Номер=doc_number, Дата=operated_at, Сумма=amount 2 знака, Плательщик1/
  Получатель1=контрагент, НазначениеПлатежа=description), КонецДокумента/
  КонецФайла.
- Честная оговорка: соответствие полей проверить загрузкой в реальной 1С
  на пилоте — добить недостающие реквизиты итерацией.

**Приёмка E**: файл за период содержит только нужные транзакции, кодировка
cp1251 читается, сторно-пары и помеченные удалением отсутствуют; после
проставления account_number выгрузка перестаёт возвращать 422.

## Этап F (опционально, можно следующим коммитом) — мини-исполнитель рецептов

`recipes.definition` обретает исполнение: `{trigger_event, action:
{type: 'api_call', connection_id, method, endpoint, body_template,
token…}}`. Обработчик триггера рендерит body из payload и дергает
внутренний connection типа http_rest (base_url `http://api:8000`) с
X-API-Token из этапа A. Сеть — только через коннектор (ADR-001).

## Общие требования

- Регресс: ruff (импорты сети только в connectors/**; обработчики ходят
  через коннектор), pytest (парсер CBR на fixture-XML с Nominal=10,
  cron-логика, рендер шаблонов, кодировка 1C-файла, токен-auth), smoke
  (+секции: токен, курс из ЦБ, правило Telegram на мок, экспорт 1С).
- Все новые события — с контрактами полей (ADR-002), деньги строками
  (ADR-003). UI-строки — i18n-ключи.
- Non-goals: входящий фид реального банка, UI экранов учёта (следующий
  большой блок), CommerceML, приём webhook'ов Telegram.
