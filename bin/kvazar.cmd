@echo off
setlocal EnableDelayedExpansion
rem CLI-диспетчер коробки Квазар (box-installer-spec §3.11-3.12, этап B).
rem Стек — проект kvazar; все команды: прогресс, OK/ERROR, лог.
rem kvazar backup | kvazar update | kvazar restore X --yes | status | stop | start

set "KVAZAR_HOME=%USERPROFILE%\Documents\Quasar"
set "STACK=%KVAZAR_HOME%\stack"
set "COMPOSE=docker compose -f "%STACK%\docker-compose.box.yml" --project-name kvazar"
set "LOGDIR=%KVAZAR_HOME%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOGFILE=%LOGDIR%\kvazar-cli.log"

if "%~1"=="" goto :help
for /f "tokens=1-3,*" %%a in ("%*") do set "SUB=%%a" & set "ARG1=%%b" & set "ARG2=%%c"

echo [%DATE% %TIME%] kvazar %* >>"%LOGFILE%"

if /i "%SUB%"=="status"   goto :status
if /i "%SUB%"=="stop"     goto :stop
if /i "%SUB%"=="start"    goto :start
if /i "%SUB%"=="update"   goto :update
if /i "%SUB%"=="backup"   goto :backup
if /i "%SUB%"=="restore"  goto :restore
echo ERROR: неизвестная команда "%SUB%"
goto :help

:status
echo === Состояние Квазара ===
%COMPOSE% ps
echo.
echo Health-check:
curl -sf -o nul -w "  web/api: HTTP %%{http_code}\n" http://localhost:8080/health 2>nul
if errorlevel 1 echo   web/api: НЕ ОТВЕЧАЕТ ^(стек поднимается или остановлен^)
echo   версия: 
curl -sf http://localhost:8080/api/v1/system/version 2>nul
if errorlevel 1 echo   (неизвестна)
echo.
goto :eof

:stop
echo Останавливаем Квазар…
%COMPOSE% down && echo OK: стек остановлен || (echo ERROR: см. вывод выше & exit /b 1)
goto :eof

:start
echo Запускаем Квазар…
%COMPOSE% up -d || (echo ERROR: см. вывод выше & exit /b 1)
echo Ожидаем готовность (до 3 минут)…
call :wait_health 180
goto :eof

:update
echo === Обновление Квазара ===
echo 1/4 Проверяем обновления и подпись манифеста…
docker compose -f "%STACK%\docker-compose.box.yml" --project-name kvazar run --rm ^
  -e UPDATE_MANIFEST_URL api python deploy/update.py --pull --yes 2>&1 | tee -a "%LOGDIR%\update.log"
if errorlevel 1 (
  echo ERROR: обновление не выполнено — авто-откат сработал или подпись не прошла.
  echo        Журнал: %LOGDIR%\update.log
  exit /b 1
)
echo 2/4 Обновление применено. Проверяем здоровье…
call :wait_health 120 || (echo ERROR: health-check после обновления — см. update.log & exit /b 1)
echo OK: обновление завершено
goto :eof

:backup
echo === Бэкап Квазара ===
echo 1/3 Создаём дамп базы и шифруем…
for /f "delims=" %%i in ('%COMPOSE% exec -T api python -m src.backup create --kind manual 2^>^&1') do set "BJSON=%%i"
if not defined BJSON (
  echo ERROR: бэкап не создан — стек запущен?
  exit /b 1
)
echo     %BJSON%
for /f "tokens=2 delims=:," %%f in ("!BJSON:") do set "FNAME=%%~f"
set "FNAME=!FNAME:"=!"
echo 2/3 Копируем в папку пользователя…
if not exist "%KVAZAR_HOME%\backups" mkdir "%KVAZAR_HOME%\backups"
%COMPOSE% cp api:/backups/!FNAME! "%KVAZAR_HOME%\backups\" >nul 2>&1
set "STAMP=%DATE:~6,4%%DATE:~3,2%%DATE:~0,2%-%TIME:~0,2%%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
ren "%KVAZAR_HOME%\backups\!FNAME!" kvazar-%STAMP%.enc 2>nul
ren "%KVAZAR_HOME%\backups\!FNAME!" kvazar-%STAMP%.enc 2>nul
echo 3/3 Проверяем целостность (test-restore)…
for /f "tokens=2 delims=:," %%b in ("!BJSON:") do set "BID=%%~b"
set "BID=!BID:"=!"
%COMPOSE% exec -T api python -m src.backup verify !BID! | findstr /C:"verified" >nul
if errorlevel 1 (
  echo WARNING: verify не прошёл — бэкап создан, но проверка неудачна ^(см. выше^)
) else (
  echo OK: бэкап проверён: %KVAZAR_HOME%\backups\
)
goto :eof

:restore
if "%ARG1%"=="" (
  echo Использование: kvazar restore "C:\путь\к\бэкапу.enc" --yes
  echo Сначала kvazar backup покажет данные бэкапа — вы увидите организацию.
  exit /b 1
)
if not "%ARG2%"=="--yes" (
  echo ВНИМАНИЕ: restore ПОЛНОСТЬЮ заменяет текущую базу данных.
  echo Файл: %ARG1%
  echo Подтвердите: kvazar restore "файл" --yes
  exit /b 1
)
if not exist "%ARG1%" (
  echo ERROR: файл не найден: %ARG1%
  exit /b 1
)
echo === Восстановление Квазара ===
echo 1/5 Останавливаем приложение…
%COMPOSE% stop api worker beat web >nul 2>&1
echo 2/5 Копируем файл бэкапа в стек…
for %%f in ("%ARG1%") do set "BFNAME=%%~nxf"
docker cp "%ARG1%" kvazar-api-1:/backups/!BFNAME!
echo 3/5 Восстанавливаем базу…
%COMPOSE% run --rm api python -m src.backup restore --backup-id "!BFNAME:.enc=!" --target erp --drop 2>&1 | tee -a "%LOGDIR%\restore.log"
if errorlevel 1 (
  echo ERROR: восстановление не удалось — журнал %LOGDIR%\restore.log
  exit /b 1
)
echo 4/5 Запускаем стек…
%COMPOSE% up -d >nul 2>&1
echo 5/5 Проверяем здоровье…
call :wait_health 180
echo OK: восстановление завершено
goto :eof

:wait_health
set /a WAITED=0
:wait_loop
curl -sf -o nul http://localhost:8080/health 2>nul && (echo OK: Квазар готов — http://localhost:8080 & goto :eof)
set /a WAITED+=5
if %WAITED% GEQ %1 (
  echo ERROR: Квазар не ответил за %1 секунд — журнал: %LOGFILE%
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto :wait_loop

:help
echo Квазар — команды коробки:
echo   kvazar status                 — состояние стека, health, версия
echo   kvazar update                 — обновление (бэкап + подпись + авто-откат)
echo   kvazar backup                 — бэкап в Документы\Quasar\backups + проверка
echo   kvazar restore "файл" --yes   — восстановление из бэкапа
echo   kvazar stop / kvazar start    — остановка/запуск стека
echo Журналы: %LOGDIR%
