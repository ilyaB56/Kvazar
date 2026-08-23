# Спецификация: бэкапы + обновления (фаза 1 по ADR-004)

Задание для разработки, три этапа (коммит на каждый): A — бэкапы,
B — релизные инструменты, C — оркестратор обновления. Основано на ADR-004
и docs/security-plan.md (P1 «бэкапы»).

Важное уточнение к ADR-004 (зафиксировать в README): в фазе 1 **применение
обновления — командой на хосте** (`python deploy/update.py`); кнопка в UI
показывает версию/changelog и запускает *проверку*, полный one-click (агент
с docker-socket) отложен в фазу 2 по соображениям безопасности.

## Общее

- Единый источник версии: `src/__init__.py::__version__` (сейчас «0.1.0»),
  убрать дублирование из `main.py`; версия API-образа = тегу git/образа.
- Новый env: `BACKUP_KEY` (Fernet-ключ, отдельный от `SECRETS_KEY` — ротация
  SecretsKey не должна ломать старые бэкапы), `UPDATE_MANIFEST_URL`
  (дев-дефолт: локальный файл `file://deploy/test-manifest.json`),
  `BACKUP_DIR` (volume), `BACKUP_RETENTION=3`, `BACKUP_SCHEDULE="03:00"`.
- Исключение ADR-001 для сети: проверки обновлений ходят в интернет из
  `src/core/update/` — добавить per-file-ignores TID251 для этого пакета
  с комментарием «инфраструктурное исключение, ADR-001».

## Этап A — система бэкапов

**Сервис `src/core/backup.py`** (слои по ADR-005):
- `create_backup(kind: manual|scheduled|pre_update)` → `pg_dump` (через
  `subprocess` в контейнере db; формат — custom `-Fc`), затем шифрование
  файла (Fernet по `BACKUP_KEY`, потоково), запись в `erp_core.backups`:
  id, file_name, size, sha256 (считается до шифрования), kind, status,
  created_at. Аудит-событие `backup.created`.
- `restore_backup(backup_id, target_db)` — расшифровка + восстановление;
  production-restore выполняет хост-скрипт (этап C), из сервиса —
  восстановление в служебную БД `erp_verify` для проверок.
- `verify_backup(backup_id)`: расшифровать (ключ/формат живы) + sha256 +
  test-restore в `erp_verify` + sanity-запрос (`SELECT count(*) FROM
  erp_core.users`) → статус в `backups.status` (`verified/failed`).
- Retention: при создании удалять самые старые сверх `BACKUP_RETENTION`
  (файл + строка; last N каждого вида не делим — просто N последних).
- Расписание: beat-задача ежедневно в `BACKUP_SCHEDULE` (перезапуск beat
  читает актуальное значение); ежемесячная задача verify самого свежего.

**API (admin)**: `GET /api/v1/system/backups`, `POST /api/v1/system/backups`
(ручной запуск, 202), `POST /api/v1/system/backups/{id}/verify` (202),
`GET /api/v1/system/version` (текущая версия, канал, latest — после этапа C).

**UI**: раздел «Обновления и бэкапы»: список бэкапов (дата, размер, вид,
статус), кнопки «Создать бэкап», «Проверить целостность» (статус обновляется
поллингом).

**Приёмка A**: создание → зашифрованный файл + строка; `verify` зелёный;
расписание срабатывает (проверить сдвигом времени задачи); retention
оставляет 3; расшифровать вручную openssl/python можно только с BACKUP_KEY.

## Этап B — релизные инструменты

`tools/release.py` (наша сторона, не поставляется клиенту):
- `build`: собрать `manifest.json` — {version, channel: stable|beta,
  changelog (текст, RU), min_supported, images: {api,worker,beat,web:
  {repo, digest}}, created_at}; посчитать дайджесты образов (`docker
  inspect` по тегу). Подпись Ed25519 поверх каноничного JSON манифеста.
- `sign` / `verify` (verify — одна функция, используется и в оркестраторе).
- Ключи: приватный `RELEASE_KEY_PATH` (env, вне репо); публичный
  `deploy/keys/update-public.pem` — в репо (прошивается в дистрибутив).
- `gen-test-manifest`: сгенерировать дев-манифест для локальной отладки
  (file:// URL), в т.ч. версию «выше текущей» с фейковым changelog.

**Приёмка B**: `verify` проходит на подписанном манифесте; испорченный
символ / чужой ключ / отсутствие подписи → отказ с понятной ошибкой.

## Этап C — оркестратор обновления

`deploy/update.py` — скрипт на хосте (docker CLI + compose), шаги по ADR-004:

1. Загрузить манифест (`UPDATE_MANIFEST_URL`, http(s) или file:// для дева),
   **проверить подпись и min_supported ДО каких-либо действий**; меньше —
   вежливый отказ с инструкцией про цепочку обновлений.
2. Pre-flight: место на диске (>= 2× текущей БД), `docker compose ps`
   зелёный, доступность БД.
3. Авто-бэкап pre_update (через `docker compose exec api python -m src.backup
   create --kind pre_update`), дождаться строки в БД; провал — стоп.
4. `docker compose pull` (или `docker tag` в дев-симуляции) по дайджестам;
   несовпадение дайджеста → стоп.
5. `docker compose up -d` (миграции применит api при старте, транзакционно).
6. Health-check: `/health` + `/api/v1/modules` до 120 с. Не поднялся →
   **авто-откат**: `docker compose down` → восстановление pre_update-бэкапа
   в основную БД → `up -d` на прежних тегах (сохранённых в
   `deploy/.erp-state.json`: current_version, previous_images) → повторный
   health-check → отчёт.
7. Итог в `deploy/update.log` + событие `system.updated`/`system.rollback`
   через API в новую (или откаченную) систему.

**Проверка наличия обновлений (внутри системы)**: beat-раз в 24 ч — Celery
задача `src/core/update/check.py`: манифест по URL, verify, сравнение версий
(semver), результат — в `erp_core.settings` (`update.available`) + событие;
кнопка «Проверить сейчас» в UI (admin) + карточка «Текущая X / Доступна Y»
с changelog и предупреждением «обновление выполняется командой на сервере».

**Приёмка C (dev-симуляция, без реального registry)**:
1. Собрать два локальных тега `erp-api:0.1.0` (текущий) и `erp-api:0.1.1`
   (с тривиальной аддитивной миграцией 0005-test и bump `__version__`);
   тест-манифест `gen-test-manifest`.
2. Полный цикл: update.py → бэкап создан → образы заменены → миграция
   применена → health ok → `/api/v1/system/version` = 0.1.1, smoke зелёный.
3. Откат: подложить «сломанный» образ (up сразу падает) → оркестратор
   вернул 0.1.0 + восстановил бэкап → health ok, smoke зелёный, в логе
   `system.rollback`.
4. Безопасность: tamper-манифест → отказ до бэкапа; version < min_supported →
   отказ с текстом про цепочку.
5. Регресс: ruff (с per-file-ignores), pytest (unit: semver-сравнение,
   verify манифеста, retention), smoke — зелёные; README: раздел
   «Обновление и бэкапы» (режимы, ключи, процедура восстановления руками).

## Non-goals фазы 1

One-click кнопка применения (фаза 2, docker-socket агент), S3-выгрузка
бэкапов (P2, опция позже), каналы в UI (один URL на окружение), автоматное
расписание произвольных времён (только ежедневное HH:MM), rollback данными
через alembic (запрещён ADR-004).
