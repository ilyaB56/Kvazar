@echo off
rem CLI-диспетчер коробки Квазар (box-installer-spec §3.11–3.12, этап A:
rem обёртки update/backup/restore/status). Стек — проект quasar.
setlocal
set COMPOSE=docker compose -f "%USERPROFILE%\Documents\Quasar\stack\docker-compose.box.yml" --project-name quasar

if "%~1"=="" goto :help
if /i "%~1"=="status" (
  %COMPOSE% ps
  curl -sf http://localhost:8080/api/v1/health >nul 2>&1 && echo Health: OK || echo Health: НЕ ОТВЕЧАЕТ
  goto :eof
)
if /i "%~1"=="update" (
  echo Проверяем обновления (ADR-004: бэкап перед обновлением, авто-откат)…
  docker compose -f "%USERPROFILE%\Documents\Quasar\stack\docker-compose.box.yml" --project-name quasar run --rm api python deploy/update.py 2>&1 | tee -a "%USERPROFILE%\Documents\Quasar\logs\update.log"
  goto :eof
)
if /i "%~1"=="backup" (
  echo Создаём бэкап…
  %COMPOSE% exec -T api python -m src.backup create
  docker compose -f "%USERPROFILE%\Documents\Quasar\stack\docker-compose.box.yml" --project-name quasar cp api:/backups/. "%USERPROFILE%\Documents\Quasar\backups\" 2>nul
  echo Файлы бэкапов: %USERPROFILE%\Documents\Quasar\backups\
  goto :eof
)
if /i "%~1"=="restore" (
  if "%~2"=="" ( echo Использование: quasar restore ^<файл.quasarbak^> --yes & goto :eof )
  echo ВНИМАНИЕ: восстановление ПОЛНОСТЬЮ заменяет текущую базу данных.
  echo Файл: %~2
  if not "%~3"=="--yes" ( echo Подтвердите: quasar restore "файл" --yes & goto :eof )
  %COMPOSE% stop api worker beat web
  docker cp "%~2" quasar-api-1:/backups/
  %COMPOSE% run --rm api python -m src.backup restore --backup-id "%~n2" --drop
  %COMPOSE% up -d
  goto :eof
)

:help
echo Квазар — команды коробки:
echo   quasar status              — состояние стека
echo   quasar update              — обновление (бэкап + авто-откат)
echo   quasar backup              — ручной бэкап в Documents\Quasar\backups
echo   quasar restore файл --yes  — восстановление из бэкапа
