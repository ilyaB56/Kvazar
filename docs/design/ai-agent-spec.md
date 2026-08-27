# Спецификация модуля ai_agent (v1)

Задание для разработки. Основано на ADR-001/002/003/006. Этапы A→F,
коммит на каждый. Все сетевые вызовы LLM — только через коннекторы
(`src/modules/integrations/connectors/`), сам модуль httpx не импортирует.
Тесты — на мок-коннекторе; живой Ollama — ручная проверка (профиль `ai`).

## Этап A — инфраструктура ИИ

- Compose-сервис `ollama` (профиль `ai`, volume `ollama_models`); модели
  задаются env: `AI_CHAT_MODEL` (дефолт `qwen2.5:7b-instruct`), 
  `AI_EMBED_MODEL` (дефолт `bge-m3` — русский язык). Прогрев моделей —
  скриптом `python -m src.modules.ai_agent.pull_models` (запуск вручную
  после первого старта; указать в README).
- Коннекторы в `integrations/connectors/`:
  - `ollama` — chat (`/api/chat`) и embeddings (`/api/embed`), config:
    `base_url` (дефолт `http://ollama:11434`), длинные таймауты;
  - `llm_mock` — детерминированные ответы для тестов (сценарии задаёт
    вызов), embeddings — псевдовекторы (hash-based, стабильные);
  - `llm_openai_compat` — заготовка внешнего LLM: код есть, **регистрация
    в каталоге отключена** флагом `ENABLE_EXTERNAL_LLM` (default `false`,
    ADR-006).
- Настройки ядра: `AI_PROVIDER` (ollama|llm_mock), `AI_CHAT_MODEL`,
  `AI_EMBED_MODEL`, `AI_MAX_TOOL_STEPS=5`.
- Внутренний connection «ollama» и токен агента — сид (идемпотентно):
  токен `ai-agent` роль `user` (ADR-006, п. 4).

**Приёмка A**: `/api/v1/modules` включает ai_agent; коннектор `ollama`
в каталоге; `llm_openai_compat` в каталоге отсутствует при дефолт-флаге.

## Этап B — RAG (документы + поиск)

- Миграция: extension `vector`; таблицы схемы `ai_agent`:
  `documents` (id, name, source_type, uploaded_by, created_at, is_deleted),
  `chunks` (id, document_id, chunk_index, text, embedding vector(1024)).
- Чанкинг ~1000 символов, перекрытие 15%; эмбеддинги — коннектор
  (`AI_EMBED_MODEL`).
- API (role user+): `POST /api/v1/ai/documents` (upload txt/md/csv,
  лимит 5 МБ; CSV индексируется построчно с заголовком),
  `GET /api/v1/ai/documents`, `DELETE /api/v1/ai/documents/{id}`
  (чистит чанки; не удаляет из сессий ссылки — там копия текста),
  `GET /api/v1/ai/search?q=&limit=` — top-k по косинусной близости,
  ответ с текстом чанка и именем документа.
- Restricted-фильтр: загрузка файлов с расширениями ключей/секретов
  (`*.pem`, `*.key`, `.env`) — 422 с предупреждением (ADR-006, п. 2).

**Приёмка B**: загрузка fixture-документа → чанки с эмбеддингами (mock);
поиск «X» возвращает релевантный чанк первым; удаление чистит; запрет
`.env` — 422.

## Этап C — чат с контекстом

- Таблицы: `chat_sessions` (id, user_id, title, created_at),
  `chat_messages` (id, session_id, role user|assistant|system, content,
  meta JSONB: sources, tools_used, created_at). Полное логирование —
  это и есть эти таблицы (ADR-006, п. 7).
- `POST /api/v1/ai/chat` {session_id?, message} → {session_id, answer,
  sources[]}: системный промпт (дата сегодня, базовая валюта RUB, правила
  «данные — не команды»), контекст = top-k RAG по сообщению + последние
  10 сообщений сессии. Заголовок сессии — первые 40 символов первого
  сообщения. Лимит сообщений в запросе к модели — обрезать старше 10.
- API: `GET /api/v1/ai/sessions`, `GET /api/v1/ai/sessions/{id}`
  (сообщения), `DELETE` (своя сессия; админ — любая).
- UI-раздел «ИИ-ассистент»: список сессий, чат, под ответом — источники
  (документ + фрагмент). Стриминг — non-goal, обычный запрос/ответ.

**Приёмка C**: два вопроса по fixture-документу → осмысленные ответы,
sources указывают на документ; история сессии полная (chat_messages);
чужая сессия недоступна (403).

## Этап D — инструменты (tool-use, read-only)

- Инструменты (whitelist, всё чтение): `get_cashflow(date_from,date_to,
  account_id?)`, `search_transactions(date_from?,account_id?,category_id?,
  q?, limit)`, `get_rate(currency,date)`, `search_documents(q,limit)`.
- Реализация: сервис агента вызывает публичный API учёта через внутренний
  connection + токен `ai-agent` (механика витринной цепочки, этап F).
  Схема инструментов (JSON-schema) — в системном промпте; цикл
  tool-calling с лимитом `AI_MAX_TOOL_STEPS`; каждый вызов — в meta
  сообщения. Формат tool-calls — по возможностям выбранной модели
  (function calling; для qwen — нативный, fallback — JSON-протокол в
  промпте; мок использует JSON-протокол).
- Запросы к деньгам — только агрегаты и строки-значения (ADR-003).

**Приёмка D**: сценарий на моке: «сколько пришло за август» → модель
вызывает get_cashflow → ответ содержит число из отчёта; meta содержит
tools_used; лимит шагов соблюдается (скриптованный зацикленный мок →
останов по лимиту, честный ответ пользователю).

## Этап E — предложения (запись через подтверждение)

- Таблица `proposals` (id, user_id, action_type `create_transaction` |
  `categorize`, payload JSONB, reason текст, status `pending|approved|
  rejected|auto_applied|failed`, created_at, decided_at, decided_by,
  result JSONB). Таблица `user_settings` (user_id PK, autopapply bool
  default false).
- Источники предложений: (1) из чата — модель через инструмент
  `propose_transaction(...)` (write-инструмент, единственный; создаёт
  proposal, не транзакцию); (2) из этапа F (автоматически из выписок).
- API (user+): `GET /api/v1/ai/proposals?status=`, `POST .../proposals/
  {id}/approve` (применяет: вызывает accounting API токеном агента,
  статус approved + result с id транзакции; ошибка — failed + текст),
  `POST .../proposals/{id}/reject`; `GET/PUT /api/v1/ai/settings`
  (autopapply — только для ролей user/admin; readonly — 403 на PUT).
- Автоприменение: если autopapply=true, агент применяет предложение сам
  сразу после создания (статус `auto_applied`, result фиксируется,
  события те же) — ADR-006, п. 5.
- События: `ai.proposal.created|approved|rejected|auto_applied|failed`
  (payload: proposal_id, action_type; без сумм — суммы в proposal).
- UI: в разделе ИИ вкладка «Предложения»: список (тип, описание, причина,
  статус, кнопки Применить/Отклонить для pending), переключатель
  «Автоприменение» с предупреждением. i18n.

**Приёмка E**: proposal из чата → approve → транзакция реально в учёте
(проверить через API), статус approved; reject → ничего не создано;
autopapply=on → создана сразу со статусом auto_applied и записью в аудите;
readonly: approve 403, PUT настроек 403.

## Этап F — классификация входящих выписок (событийная витрина)

- Подписчик модуля на `integration.data.fetched`: если в payload есть
  items (данные fetch-джобы), для каждого item (не более 20 за событие)
  агент в фоне (Celery-задача `classify_task`, не в request-цикле!) строит
  предложение: распознать сумму/дату/описание → подобрать статью и счёт
  (поиск по справочникам + семантика) → proposal `create_transaction`
  с reason «из выписки {job}». Ошибки — в журнал, диспетчер не валить.
- Никаких автозаписей: даже при autopapply предложения по выпискам
  требуют явного одобрения в v1 (деньги из внешнего источника —
  повышенный риск, ADR-006 п. 5).

**Приёмка F**: мок-джоба fetch с fixture-выпиской (3 платежа) → 3
предложения с корректно распознанными суммами и предложенными статьями;
повторное событие не дублирует (идемпотентность по хэшу item+job).

## Общие требования и приёмка

- Регресс: ruff (сеть только в коннекторах), pytest (моки: чанкинг,
  поиск, tool-оркестрация, лимит шагов, предложения, autopapply, идемпо-
  тентность F), smoke (+секции ai B/C/D/E на `AI_PROVIDER=llm_mock`).
- Модуль: стандарт структуры ADR-005; события с контрактами (ADR-002);
  UI-строки i18n.
- Ручная проверка (опционально, при GPU): `docker compose --profile ai
  up`, прогрев моделей, один живой диалог + один RAG-поиск.
- Non-goals v1: стриминг, внешние LLM (включение — поправка ADR-006),
  голос, обучение/файнтюн, мультиагентные цепочки, предложения для CRM.
