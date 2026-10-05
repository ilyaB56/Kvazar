; Квазар — коробочный установщик v1 (box-installer-spec, УТВЕРЖДЕНА
; 2026-09-26; этап A). Inno Setup 6.x (вариант Р1 §3.2).
;
; ИЗМЕНЕНИЕ 2026-10-03 (решение основателя): Docker Desktop ставится
; ЯВНО, своим штатным мастером (тихая установка удалена после серии
; инцидентов: права, WSL2, PATH, циклы перезагрузки). Установщик
; Квазара: объясняет, скачивает с прогрессом, запускает мастер,
; ждёт движок, дальше — образы Квазара, база, мастер организации.
; Сборка: iscc kvazar-setup.iss (файлы стека — рядом:
; docker-compose.box.yml, docker-compose.ai.yml,
; deploy/keys/update-public.pem, bin/kvazar.cmd).

#define AppName "Квазар"
#define AppVersion "1.0.0"
#define AppPublisher "Квазар ERP"
#define AppURL "http://localhost:8080"

[Setup]
AppId={{8E4C2A1F-6B7D-4E3A-9F52-QUASARBOX01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={userdocs}\Quasar
; per-user каталог (§3.1): запись без постоянной элевации; Docker
; Desktop и так per-user
PrivilegesRequired=lowest
; один установщик одновременно (§4 ошибочные: мьютекс)
SetupMutex=QuasarSetupMutex
DisableProgramGroupPage=yes
OutputBaseFilename=kvazar-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; русский мастер
ShowLanguageDialog=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
ru.SysCheckTitle=Проверка системы
ru.RuntimeStep=Устанавливаем среду выполнения…%nЭто может занять несколько минут
ru.ImagesStep=Загружаем компоненты Квазара…
ru.DbStep=Запускаем базу данных…
ru.ShellStep=Устанавливаем приложение Квазар…
ru.SetupStep=Применяем настройки…
ru.OrgTitle=Первая настройка Квазара
ru.OrgSubtitle=Создайте организацию и учётную запись администратора
ru.WeakPassword=Пароль слишком простой: минимум 8 символов, буквы и цифры

[Files]
; стек коробки (compose из registry + публичный ключ обновлений).
; Пути — относительно каталога скрипта (deploy/installer/): compose в
; корне репозитория, ключ — в deploy/keys/
Source: "..\..\docker-compose.box.yml"; DestDir: "{app}\stack"; Flags: ignoreversion
Source: "..\..\docker-compose.ai.yml"; DestDir: "{app}\stack"; Flags: ignoreversion
Source: "..\keys\update-public.pem"; DestDir: "{app}\stack"; Flags: ignoreversion
Source: "..\..\bin\kvazar.cmd"; DestDir: "{app}\bin"; Flags: ignoreversion

[Tasks]
; Приложение «Квазар» для Windows (tauri-shell-spec §12.3, этап C):
; включено по умолчанию для владельца. Сам установщик оболочки не
; бандлится (качается свежий из релиза визитки kvazar-download),
; скачивание и тихая доустановка — в [Code] InstallShell
Name: "shell"; Description: "Приложение «Квазар» для Windows (рекомендуется)"

[Dirs]
; Inno не допускает несколько Name: в одной строке — каждая запись отдельно
Name: "{app}\backups"
Name: "{app}\logs"

[Icons]
; ярлык «Квазар» → интерфейс (§3.9)
Name: "{userdesktop}\Квазар"; Filename: "{#AppURL}"
Name: "{userprograms}\Квазар"; Filename: "{#AppURL}"
Name: "{userprograms}\Квазар\Обновить Квазар"; Filename: "{app}\bin\kvazar.cmd"; Parameters: "update"

[Code]
var
  LogPath: string;
  OrgPage: TInputQueryWizardPage;
  AiChecked: Boolean;

procedure Log(Line: string);
begin
  SaveStringToFile(LogPath, Format('%s %s'#13#10, [
    GetDateTimeString('yyyy-mm-dd hh:nn:ss', '.', ':'), Line]), True);
end;

function InitializeSetup(): Boolean;
begin
  LogPath := ExpandConstant('{tmp}\kvazar-setup.log');
  Log('=== Установка Квазар {#AppVersion} ===');
  Result := True;
end;

// ---------- §3.4 Проверка системы ----------

function TotalRamGb(): Integer;
var
  locator, wmi, items, item: Variant;
  i: Integer;
begin
  Result := 0;
  try
    locator := CreateOleObject('WbemScripting.SWbemLocator');
    wmi := locator.ConnectServer('.', 'root\cimv2');
    items := wmi.ExecQuery('SELECT TotalPhysicalMemory FROM Win32_ComputerSystem');
    // два подводных камня Pascal Script: (1) член — только от переменной,
    // не цепочкой от вызова; (2) WMI отдаёт uint64 СТРОКОЙ (OleStr), и
    // арифметика над ним идёт через Int32 → Overflow на >2 ГБ. Лечим
    // явным StrToFloat (строка без разделителей — безопасно в любой локали)
    for i := 0 to items.Count - 1 do
    begin
      item := items.ItemIndex(i);
      Result := Round(StrToFloat(item.TotalPhysicalMemory) / 1073741824);
    end;
  except
    Log('WMI RAM: ' + AddPeriod(GetExceptionMessage));
  end;
end;

// Проверка VT-x через WMI УДАЛЕНА (инцидент 2026-10-03): флаг
// VirtualizationFirmwareEnabled маскируется под Hyper-V/WSL2 и ложно
// равен False ровно на целевых машинах коробки — диалог «не удалось
// подтвердить виртуализацию» показывался на каждом запуске и не давал
// ставить. Реальный гейт — запуск движка: если VT выключен в BIOS,
// среда выполнения не поднимется, и сообщение об этом — в финальной
// ошибке ожидания движка

function DockerExe(): string;
var
  p: string;
begin
  // PATH НАШЕГО процесса не обновляется установкой Docker (окружение
  // наследуется до неё) — после установки CLI ищем по известным путям
  Result := 'docker';
  p := ExpandConstant('{commonpf}\Docker\Docker\resources\bin\docker.exe');
  if FileExists(p) then
    Result := p
  else
  begin
    p := ExpandConstant('{localappdata}\Docker\resources\bin\docker.exe');
    if FileExists(p) then
      Result := p;
  end;
end;

function RunDocker(Args: string; var Code: Integer): Boolean;
var
  exe: string;
begin
  // вызовы docker/info — скрытые; compose (pull/up) — ВИДИМЫЕ: консоль
  // с родным прогрессом docker (инцидент «вечная загрузка образов» —
  // скрытый pull без прогресса и таймаута неотличим от зависания)
  if Pos('compose', Args) = 1 then
  begin
    exe := DockerExe();
    if exe = 'docker' then
      Result := Exec(ExpandConstant('{cmd}'), '/C docker ' + Args, '',
        SW_SHOW, ewWaitUntilTerminated, Code)
    else
      Result := Exec(exe, Args, '', SW_SHOW, ewWaitUntilTerminated, Code);
  end
  else if Pos('info', Args) = 1 then
  begin
    exe := DockerExe();
    if exe = 'docker' then
      Result := Exec(ExpandConstant('{cmd}'),
        '/C docker ' + Args + ' >nul 2>&1', '',
        SW_HIDE, ewWaitUntilTerminated, Code)
    else
      Result := Exec(exe, Args, '', SW_HIDE, ewWaitUntilTerminated, Code);
  end
  else
  begin
    exe := DockerExe();
    if exe = 'docker' then
      Result := Exec(ExpandConstant('{cmd}'),
        '/C docker ' + Args + ' >nul 2>&1', '',
        SW_HIDE, ewWaitUntilTerminated, Code)
    else
      Result := Exec(exe, Args, '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

function DockerReady(): Boolean;
var
  code: Integer;
begin
  RunDocker('info', code);
  Result := (code = 0);
end;

// NextButtonClick — в конце файла (после BootstrapOrg): Pascal требует
// объявления до использования

// ---------- §3.5 Среда выполнения (никогда «Docker») ----------

function WaitRuntimeReady(TimeoutSec: Integer): Boolean;
var
  waited: Integer;
begin
  Result := False;
  waited := 0;
  while waited < TimeoutSec do
  begin
    if DockerReady() then
    begin
      Result := True;
      Exit;
    end;
    Sleep(3000);
    waited := waited + 3;
  end;
end;

// Решение основателя 2026-10-03: Docker Desktop ставится ЯВНО, своим
// штатным мастером. Тихая установка удалена после 6 итераций инцидентов
// (код 3 без прав, PATH-слепота, пустой каталог без WSL2, циклы
// «перезагрузите»). FindDockerDesktop/WslPresent/EnsureWsl убраны:
// мастер Docker сам повышает права, включает WSL2 и объясняет
// перезагрузку — всё то, что мы пытались делать тихо и ломалось.

procedure EnsureRuntime();
var
  code: Integer;
  tmp: string;
  RuntimePage: TDownloadWizardPage;
begin
  if DockerReady() then
  begin
    Log('Docker готов — пропускаем установку');
    Exit;
  end;

  // ЯВНАЯ установка Docker (решение основателя 2026-10-03): объясняем
  // и спрашиваем согласие, дальше — обычный мастер Docker
  if MsgBox('Для работы Квазара нужен Docker Desktop — бесплатная ' +
      'программа, которая запускает Квазар на вашем компьютере.' + #13#10#13#10 +
      'Сейчас мы скачаем его (~500 МБ) и откроем обычный мастер ' +
      'установки.' #13#10 +
      '• Если мастер запросит права администратора — нажмите «Да».' #13#10 +
      '• Если попросит перезагрузить компьютер — перезагрузитесь и ' +
      'запустите установку Квазара снова, она продолжится.' + #13#10#13#10 +
      'Продолжить?', mbConfirmation, MB_YESNO) = IDNO then
    Abort;

  // .wslconfig ДО первого старта Engine при RAM ≤ 8 ГБ (§3.5.4,
  // инцидент 2026-09-11: OOM на дешёвом железе)
  if TotalRamGb() <= 8 then
  begin
    SaveStringToFile(ExpandConstant('{userdocs}\..\.wslconfig'),
      '[wsl2]'#13#10'memory=5GB'#13#10'swap=2GB'#13#10, False);
    Log('.wslconfig записан (RAM ≤ 8 ГБ: memory=5GB, swap=2GB)');
  end;

  WizardForm.StatusLabel.Caption := 'Скачиваем Docker Desktop (~500 МБ)…';
  tmp := ExpandConstant('{tmp}\runtime-installer.exe');
  Log('Скачиваем Docker Desktop (~500 МБ)…');
  // страница загрузки с прогрессом и отменой (не DownloadTemporaryFile
  // с nil-колбэком — то скачивание шло без индикации, инцидент 5 часов)
  RuntimePage := CreateDownloadPage('Docker Desktop',
    'Скачиваем Docker Desktop (~500 МБ).' #13#10 +
    'На медленном соединении — десятки минут. Если скорость нулевая ' +
    'дольше 5 минут — нажмите «Отмена», скачайте установщик Docker ' +
    'браузером с desktop.docker.com и запустите его сами, затем ' +
    'повторите установку Квазара — этот этап будет пропущен.', nil);
  RuntimePage.Add(
    'https://desktop.docker.com/win/main/amd64/Docker Desktop Installer.exe',
    'runtime-installer.exe', '');
  RuntimePage.Show;
  try
    try
      RuntimePage.Download;
    except
      Log('Скачивание Docker: ' + AddPeriod(GetExceptionMessage));
      RaiseException('Не удалось скачать Docker Desktop. Скачайте ' +
        'установщик браузером с desktop.docker.com, установите его ' +
        'и запустите, затем повторите установку Квазара.');
    end;
  finally
    RuntimePage.Hide;
  end;

  // обычный мастер Docker: сам повышает права, включает WSL2,
  // корректно просит перезагрузку — всё, что тихий режим ломал
  Log('Запускаем мастер установки Docker Desktop');
  WizardForm.StatusLabel.Caption :=
    'Следуйте мастеру установки Docker на экране.';
  ExecAsOriginalUser(tmp, 'install --accept-license', '', SW_SHOW,
    ewWaitUntilTerminated, code);
  Log(Format('Мастер Docker: код %d', [code]));

  // мастер обычно сам запускает Docker Desktop; первый старт движка
  // медленный (1–5 мин) — ждём до 10, затем инструкция + ещё 10
  WizardForm.StatusLabel.Caption :=
    'Запускаем Docker (первый старт — до 10 минут)…';
  if not WaitRuntimeReady(600) then
  begin
    Log('Docker не поднялся сразу — инструкция, второе ожидание');
    MsgBox('Docker ещё запускается.' #13#10#13#10 +
      '1) Если мастер Docker просил перезагрузку — перезагрузитесь и ' +
      'запустите установку Квазара снова, она продолжится.' #13#10 +
      '2) Иначе откройте Docker Desktop из меню «Пуск», дождитесь, ' +
      'пока иконка кита в трее станет неподвижной (статус Running), ' +
      'и нажмите «ОК» — подождём ещё 10 минут.', mbInformation, MB_OK);
    if not WaitRuntimeReady(600) then
      RaiseException('Docker не запустился. Запустите Docker Desktop из ' +
        'меню «Пуск», дождитесь статуса Running (иконка кита в трее) и ' +
        'запустите установку Квазара снова — она продолжится с этого ' +
        'места. Журнал: ' + LogPath);
  end;
  Log('Docker готов');
end;

// ---------- §3.6 Генерация .env ----------

function RunCapture(Exe, Params: string): string;
var
  code: Integer;
  tmp: string;
  buf: AnsiString; // var-параметр LoadStringFromFile — строго AnsiString
  txt: string;
begin
  Result := '';
  tmp := ExpandConstant('{tmp}\quasar-gen.out');
  Exec(ExpandConstant('{cmd}'),
    Format('/C %s %s > "%s" 2>&1', [Exe, Params, tmp]), '',
    SW_HIDE, ewWaitUntilTerminated, code);
  buf := '';
  if LoadStringFromFile(tmp, buf) then
  begin
    txt := buf;
    StringChangeEx(txt, #13#10, '', True);
    Result := txt;
  end;
end;

function NextLine(var S: string): string;
var
  p: Integer;
begin
  p := Pos(#13#10, S);
  if p = 0 then
  begin
    Result := S;
    S := '';
  end
  else
  begin
    Result := Copy(S, 1, p - 1);
    Delete(S, 1, p + 2);
  end;
end;

procedure GenerateEnv();
var
  env: string;
  jwt, secrets, pgpwd, backupKey, cors: string;
  seedpwd: string;
  exe, pyscript, outp, cmdline: string;
  buf: AnsiString;
  txt: string;
  code: Integer;
begin
  WizardForm.StatusLabel.Caption := ExpandConstant('{cm:SetupStep}');

  // повторный запуск: .env УЖЕ есть → секреты не трогаем (перегенерация
  // ломала пароль уже инициализированной базы — crash-loop worker/beat,
  // инцидент 2026-10-03)
  if FileExists(ExpandConstant('{app}\stack\.env')) then
  begin
    Log('.env существует — секреты сохранены, генерация пропущена');
    Exit;
  end;

  // Ключи генерирует контейнер уже скачанного образа Квазара: OS-PRNG
  // внутри Linux, никаких PowerShell/политик машины (инцидент «Не
  // удалось сгенерировать ключ» на ужесточённой Windows, 2026-10-03).
  // Четыре строки: fernet(SECRETS) / urlsafe48(JWT) / alnum32(PG) /
  // fernet(BACKUP)
  exe := DockerExe();
  pyscript := 'import secrets,string; from cryptography.fernet import Fernet; ' +
    'print(Fernet.generate_key().decode()); ' +
    'print(secrets.token_urlsafe(48)); ' +
    'print(''''.join(secrets.choice(string.ascii_letters+string.digits) ' +
    'for _ in range(32))); ' +
    'print(Fernet.generate_key().decode()); ' +
    'print(secrets.token_urlsafe(24))';
  outp := ExpandConstant('{tmp}\kvazar-keys.out');
  cmdline := Format('/C ""%s" run --rm --entrypoint python ' +
    'ghcr.io/ilyab56/kvazar-api:latest -c "%s" > "%s" 2>&1"', [
    exe, pyscript, outp]);
  Exec(ExpandConstant('{cmd}'), cmdline, '', SW_HIDE, ewWaitUntilTerminated, code);
  Log(Format('keygen (docker): код %d', [code]));
  buf := '';
  if (code = 0) and LoadStringFromFile(outp, buf) then
  begin
    txt := buf;
    secrets := NextLine(txt);
    jwt := NextLine(txt);
    pgpwd := NextLine(txt);
    backupKey := NextLine(txt);
    seedpwd := NextLine(txt);
  end;

  // фолбэк — PowerShell (машины, где docker run недоступен по какой-то
  // причине, но политики не ужесточены)
  if (Length(jwt) < 32) or (Length(secrets) < 32) or (Length(pgpwd) < 24) then
  begin
    Log('keygen через docker не удался — фолбэк PowerShell');
    jwt := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..48|%{Get-Random -Maximum 256}) -as [byte[]])"', '');
    secrets := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..32|%{Get-Random -Maximum 256}) -as [byte[]]).TrimEnd(''='').Replace(''+'',''-'').Replace(''/'',''_'')"', '');
    pgpwd := RunCapture('powershell -NoProfile -Command "-join((48..57)+(65..90)+(97..122)|Get-Random -Count 32|%{[char]$_})"', '');
    backupKey := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..32|%{Get-Random -Maximum 256}) -as [byte[]])"', '');
  end;

  if (Length(jwt) < 32) or (Length(secrets) < 32) or (Length(pgpwd) < 24) or
     (Length(seedpwd) < 16) then
    RaiseException('Не удалось сгенерировать ключи — журнал: ' + LogPath);

  cors := Format('["http://localhost:8080","http://%s:8080"]', [GetComputerNameString]);

  env :=
    '# Квазар — сгенерировано установщиком. СОХРАНИТЕ КОПИЮ: это ключи' + #13#10 +
    '# от ваших данных (бэкап .env = возможность восстановить доступ).' + #13#10 +
    'QUASAR_API_IMAGE=ghcr.io/ilyab56/kvazar-api:latest' + #13#10 +
    'QUASAR_WEB_IMAGE=ghcr.io/ilyab56/kvazar-web:latest' + #13#10 +
    'POSTGRES_PASSWORD=' + pgpwd + #13#10 +
    'JWT_SECRET=' + jwt + #13#10 +
    'SECRETS_KEY=' + secrets + #13#10 +
    'BACKUP_KEY=' + backupKey + #13#10 +
    // стартовый служебный админ: случайный пароль, неизвестный никому
    // (закрытие дыры дефолтного admin12345 при открытом тестировании,
    // решение основателя 2026-10-04)
    'SEED_ADMIN_PASSWORD=' + seedpwd + #13#10 +
    'BACKUP_SCHEDULE=03:00' + #13#10 +
    'WEB_TLS=0' + #13#10 +
    'CORS_ORIGINS=' + cors + #13#10 +
    // ИИ не выбран → безопасный мок-провайдер; профиль ai — этап B-чекбокс
    'AI_PROVIDER=llm_mock' + #13#10;

  SaveStringToFile(ExpandConstant('{app}\stack\.env'), env, False);
  Log('.env сгенерирован (секреты: jwt=' + IntToStr(Length(jwt)) +
    ' secrets=' + IntToStr(Length(secrets)) + ')');
end;

// ---------- §3.7–3.8 Образы, запуск, health ----------

procedure Compose(Args: string);
var
  code: Integer;
  ok: Boolean;
begin
  // через RunDocker: после установки Docker PATH нашего процесса не
  // знает docker — вызываем по полному пути (инцидент «движок не
  // поднялся» при работающем Docker, 2026-10-01)
  ok := RunDocker(Format('compose -f "%s\stack\docker-compose.box.yml" --project-name kvazar %s', [
    ExpandConstant('{app}'), Args]), code);
  if not ok or (code <> 0) then
    RaiseException(Format('Команда «%s» не удалась (код %d).' #13#10 +
      'Если ошибка про доступ к реестру (denied): образы Квазара в ' +
      'приватном GitHub Container Registry — либо сделайте пакеты ' +
      'kvazar-api/kvazar-web публичными (github.com → Packages → ' +
      'Package settings → Danger Zone), либо выполните в командной ' +
      'строке docker login ghcr.io и запустите установку снова.' #13#10 +
      'Журнал: %s', [Args, code, LogPath]));
end;

function WaitForHealth(TimeoutSec: Integer): Boolean;
var
  waited: Integer;
  code: Integer;
begin
  Result := False;
  waited := 0;
  while waited < TimeoutSec do
  begin
    // /health — корень приложения (nginx: location = /health); /api/v1/health
    // не существует и всегда 404
    Exec(ExpandConstant('{cmd}'), '/C curl -sf http://localhost:8080/health >nul 2>&1',
      '', SW_HIDE, ewWaitUntilTerminated, code);
    if code = 0 then
    begin
      Result := True;
      Exit;
    end;
    Sleep(5000);
    waited := waited + 5;
  end;
end;

// ---------- Приложение «Квазар» для Windows (tauri-shell-spec §12.3) ----------

function ShellInstalled(): Boolean;
var
  names: TArrayOfString;
  i: Integer;
  disp: string;
begin
  // идемпотентность: повторная установка коробки не дублирует оболочку.
  // Tauri NSIS (per-user) пишет ключ HKCU\...\Uninstall с DisplayName =
  // productName; латиница «Kvazar» отличает его от коробки (её Inno-ключ
  // — кириллица «Квазар»), деинсталляции независимы (§12.3, приёмка C.17)
  Result := False;
  if not RegGetSubkeyNames(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall', names) then
    Exit;
  for i := 0 to GetArrayLength(names) - 1 do
  begin
    if RegQueryStringValue(HKEY_CURRENT_USER,
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + names[i],
        'DisplayName', disp) then
      if disp = 'Kvazar' then
      begin
        Result := True;
        Exit;
      end;
  end;
end;

procedure InstallShell();
var
  page: TDownloadWizardPage;
  code: Integer;
begin
  if not WizardIsTaskSelected('shell') then
  begin
    Log('Оболочка: задача не выбрана — пропускаем');
    Exit;
  end;
  if ShellInstalled() then
  begin
    Log('Оболочка: уже установлена — пропускаем (идемпотентность)');
    Exit;
  end;
  // скачиваем СВЕЖИЙ установщик из релиза визитки kvazar-download
  // (стабильное имя ассета kvazar-shell-setup.exe налаживает CI-джоба
  // shell); НЕ external [Files]: релизы независимы от версии коробки
  WizardForm.StatusLabel.Caption := 'Скачиваем приложение Квазар…';
  page := CreateDownloadPage('Приложение Квазар',
    'Скачиваем приложение Квазар для Windows (~5 МБ).', nil);
  page.Add('https://github.com/ilyaB56/kvazar-download/releases/latest/download/kvazar-shell-setup.exe',
    'kvazar-shell-setup.exe', '');
  page.Show;
  try
    try
      page.Download;
    except
      // сбой оболочки НЕ роняет установку коробки: оболочка опциональна
      // (§1), её можно поставить позже из релиза визитки
      Log('Оболочка: скачать не удалось — ' + AddPeriod(GetExceptionMessage));
      Exit;
    end;
  finally
    page.Hide;
  end;
  // тихая доустановка ПОСЛЕ успешного WaitForHealth (§12.3): NSIS /S,
  // per-user — без UAC
  Exec(ExpandConstant('{tmp}\kvazar-shell-setup.exe'), '/S', '', SW_HIDE,
    ewWaitUntilTerminated, code);
  Log(Format('Оболочка: установщик завершился с кодом %d', [code]));
end;

// ---------- §3.9 Мастер первой настройки ----------

procedure CreateOrgWizard();
begin
  OrgPage := CreateInputQueryPage(wpInstalling,
    ExpandConstant('{cm:OrgTitle}'),
    ExpandConstant('{cm:OrgSubtitle}'),
    'Администратор сможет входить по этому email и паролю.');
  OrgPage.Add('Название организации:', False);
  OrgPage.Add('Ваше имя:', False);
  OrgPage.Add('Email:', False);
  OrgPage.Add('Пароль (≥8 символов, буквы и цифры):', True);
  OrgPage.Values[0] := '';
  OrgPage.Values[1] := '';
  OrgPage.Values[2] := '';
  OrgPage.Values[3] := '';
end;

function BootstrapOrg(): Boolean;
var
  name_, full_, email, password, args: string;
  code: Integer;
begin
  Result := False;
  name_ := OrgPage.Values[0];
  full_ := OrgPage.Values[1];
  email := OrgPage.Values[2];
  password := OrgPage.Values[3];

  // раздельные сообщения: одно «пароль слишком простой» на три разных
  // поля сбивало с толку (инцидент 2026-10-03 — длинный пароль отклонялся
  // из-за email/названия, а вина сваливалась на пароль)
  if Length(name_) < 2 then
  begin
    MsgBox('Поле «Название организации»: введите не менее 2 символов.',
      mbError, MB_OK);
    Exit;
  end;
  if Pos('@', email) = 0 then
  begin
    MsgBox('Поле «Email»: нужен адрес электронной почты со знаком @ ' +
      '(например, ivan@firma.ru).', mbError, MB_OK);
    Exit;
  end;
  if Length(password) < 8 then
  begin
    MsgBox('Поле «Пароль»: минимум 8 символов (буквы и цифры).',
      mbError, MB_OK);
    Exit;
  end;

  // Запрос отправляет контейнер образа Квазара ИЗНУТРИ сети стека:
  // SaveStringToFile пишет ANSI (cp1251) — кириллица названия/имени
  // давала битый UTF-8 и код 400 «не могу разобрать тело» (инцидент
  // 2026-10-03). env-переменные docker передаёт в UTF-8 корректно.
  // HTTP-код возвращаем КОДОМ ЗАВЕРШЕНИЯ (sys.exit); http.client не
  // бросает исключений на 4xx/5xx — try/except не нужен (однoстрочный
  // try в python невалиден — поймано тестом)
  args := Format('run --rm --network kvazar_default ' +
    '-e "ORG=%s" -e "FULL=%s" -e "EMAIL=%s" -e "PASS=%s" ' +
    'ghcr.io/ilyab56/kvazar-api:latest python -c "import os,json,sys,' +
    'http.client;' +
    'd=json.dumps({' + '''company_name'':os.environ[''ORG''],' +
    '''admin_full_name'':os.environ[''FULL''],' +
    '''admin_email'':os.environ[''EMAIL''],' +
    '''admin_password'':os.environ[''PASS'']}).encode();' +
    'c=http.client.HTTPConnection(''web'',80,timeout=60);' +
    'c.request(''POST'',''/api/v1/platform/bootstrap'',body=d,' +
    'headers={''Content-Type'':''application/json''});' +
    'sys.exit(c.getresponse().status)"', [
    name_, full_, email, password]);
  RunDocker(args, code);
  Log(Format('bootstrap (docker): код %d', [code]));
  Result := (code = 201) or (code = 200);
  if not Result then
  begin
    if code = 409 then
      MsgBox('Организация уже создана — просто войдите в Квазар ' +
        '(http://localhost:8080) с этим email и паролем.', mbInformation, MB_OK)
    else if code = 422 then
      MsgBox('Сервер отклонил пароль: добавьте буквы и цифры, длина 8+.',
        mbError, MB_OK)
    else
      MsgBox('Не удалось создать организацию (код ' + IntToStr(code) + ').' + #13#10 +
        'Журнал: ' + LogPath, mbError, MB_OK);
  end;
end;

// Inno 6.3+: 8 параметров (MemoComponentsInfo/MemoGroupInfo/MemoTasksInfo
// вместо прежнего MemoComponentsInfoLine)
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  // после установки — сразу первая настройка (§3.3 шаг 7)
  CreateOrgWizard();
  Result := MemoDirInfo;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // ssPostInstall, НЕ ssInstall: файлы [Files] копируются МЕЖДУ этими
  // событиями — на ssInstall compose-файла ещё нет на свежем каталоге
  // («pull код 1» на любом реестре, инцидент 2026-10-03). .env — до
  // любого compose: POSTGRES_PASSWORD объявлена обязательной (:?),
  // интерполяция падает без неё даже на pull
  if CurStep = ssPostInstall then
  begin
    EnsureRuntime();          // 3: среда выполнения (явно, мастером Docker)
    GenerateEnv();            // 4: .env с прод-секретами — РАНЬШЕ compose
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:ImagesStep}');
    Compose('pull');          // 5: образы из registry
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:DbStep}');
    Compose('up -d');         // 6: запуск
    Log('Ожидание health-check (до 5 мин)…');
    if not WaitForHealth(300) then
      RaiseException('Квазар не ответил за 5 минут. Журнал: ' + LogPath + #13#10 + 'После запуска среды выполнения повторите установку — она продолжит с этого места.');
    Log('Стек поднят, health зелёный');
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:ShellStep}');
    InstallShell();           // 7: приложение Квазар для Windows (этап C)
  end;
end;

// Единый обработчик «Далее»: проверки системы (wpSelectDir) + bootstrap
// первой настройки (OrgPage). Прежний NextButtonClick2 никогда не
// вызывался — имя не является событием Inno, мастер пропускал создание
// организации без проверки.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ram: Integer;
  free_mb, total_mb: Cardinal;
  winver: TWindowsVersion;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    // ОС: современный Docker Desktop требует Win10 21H2+ (сборка 19044)
    // или Win11 (22000+) — на старых ставится пустой каталог (инцидент
    // 2026-10-03)
    GetWindowsVersionEx(winver);
    if (winver.Major < 10) or ((winver.Major = 10) and
        (winver.Minor = 0) and (winver.Build < 19044)) then
    begin
      MsgBox('Квазар требует Windows 10 версии 21H2 или новее (сборка ' +
        '19044+), либо Windows 11.' #13#10 + 'Обновите Windows ' +
       ('(Параметры → Центр обновления Windows) и запустите установку ' +
        'заново. Ваша сборка: ') + IntToStr(winver.Build) + '.',
        mbError, MB_OK);
      Result := False;
      Exit;
    end;

    // RAM: ≥6 ГБ предупреждение, <4 — блок (§7.9)
    ram := TotalRamGb();
    Log(Format('RAM: %d ГБ', [ram]));
    if ram < 4 then
    begin
      MsgBox('Требуется не менее 4 ГБ оперативной памяти (рекомендуется 6+).',
        mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if ram < 6 then
      if MsgBox(Format('Обнаружено %d ГБ ОЗУ. Квазар будет работать, но ' +
        'медленно; ИИ-ассистент недоступен. Продолжить?', [ram]),
        mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
    AiChecked := ram >= 8; // ИИ-опция только на 8+ ГБ (§10.8)

    // диск ≥ 10 ГБ (InMegabytes=True → сразу мегабайты)
    GetSpaceOnDisk(ExpandConstant('{sd}'), True, free_mb, total_mb);
    Log(Format('Свободно на системном диске: %d МБ', [free_mb]));
    if free_mb < 10 * 1024 then
    begin
      MsgBox('Требуется не менее 10 ГБ свободного места на системном диске.',
        mbError, MB_OK);
      Result := False;
      Exit;
    end;
    // VT-проверка удалена: WMI-флаг ложно False на любой Hyper-V/WSL2-
    // машине (вся целевая аудитория коробки) — диалог показывался при
    // каждом запуске и не давал ставить. Реальный гейт — старт движка
  end;

  // страница первой настройки (после установки): создаём организацию
  if (OrgPage <> nil) and (CurPageID = OrgPage.ID) then
    Result := BootstrapOrg();
end;

procedure DeinitializeSetup();
begin
  Log('=== Завершение установщика ===');
  // журнал — в %TEMP% (спека: kvazar-setup.log; копируем в logs коробки
  // если установка дошла до создания каталога)
  if DirExists(ExpandConstant('{app}\logs')) then
    CopyFile(LogPath, ExpandConstant('{app}\logs\install.log'), False);
end;
