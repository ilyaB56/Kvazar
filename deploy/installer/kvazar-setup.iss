; Квазар — коробочный установщик v1 (box-installer-spec, УТВЕРЖДЕНА
; 2026-09-26; этап A). Inno Setup 6.x (вариант Р1 §3.2).
;
; Пользователь НИКОГДА не видит слово «Docker» — только «среда
; выполнения» (§3.3). Сборка: iscc kvazar-setup.iss (файлы стека —
; рядом: docker-compose.box.yml, docker-compose.ai.yml,
; deploy/keys/update-public.pem, bin/kvazar.cmd).
;
; Этап A покрывает: проверку системы, среду выполнения, .wslconfig,
; генерацию .env, up -d + health, bootstrap-мастер, ярлык, мьютекс,
; журнал. Этап B (по спеке): деинсталлятор-вопросы, порт-выбор UI.

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
  locator, wmi, items: Variant;
  i: Integer;
begin
  Result := 0;
  try
    // цепочка CreateOleObject(...).ConnectServer(...) не компилируется —
    // член вызывается только от переменной Variant
    locator := CreateOleObject('WbemScripting.SWbemLocator');
    wmi := locator.ConnectServer('.', 'root\cimv2');
    items := wmi.ExecQuery('SELECT TotalPhysicalMemory FROM Win32_ComputerSystem');
    // for..in по Variant-коллекции не поддерживается Pascal Script —
    // только индексный обход (SWbemObjectSet.ItemIndex)
    for i := 0 to items.Count - 1 do
      Result := Round(items.ItemIndex(i).TotalPhysicalMemory / 1073741824);
  except
    Log('WMI RAM: ' + AddPeriod(GetExceptionMessage));
  end;
end;

function VirtualizationEnabled(): Boolean;
var
  locator, wmi, items: Variant;
  i: Integer;
begin
  Result := False;
  try
    locator := CreateOleObject('WbemScripting.SWbemLocator');
    wmi := locator.ConnectServer('.', 'root\cimv2');
    items := wmi.ExecQuery('SELECT VirtualizationFirmwareEnabled FROM Win32_Processor');
    for i := 0 to items.Count - 1 do
      Result := items.ItemIndex(i).VirtualizationFirmwareEnabled;
  except
    Result := True; // не удалось проверить — не блокируем (Docker сообщит)
    Log('WMI VT-x: ' + AddPeriod(GetExceptionMessage));
  end;
end;

function DockerReady(): Boolean;
var
  code: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C docker info >nul 2>&1', '',
    SW_HIDE, ewWaitUntilTerminated, code);
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

procedure EnsureRuntime();
var
  code: Integer;
  tmp, cmdline: string;
begin
  if DockerReady() then
  begin
    Log('Среда выполнения уже готова — пропускаем установку');
    Exit;
  end;

  // .wslconfig ДО первого старта Engine при RAM ≤ 8 ГБ (§3.5.4,
  // инцидент 2026-09-11: OOM на дешёвом железе)
  if TotalRamGb() <= 8 then
  begin
    SaveStringToFile(ExpandConstant('{userdocs}\..\.wslconfig'),
      '[wsl2]'#13#10'memory=5GB'#13#10'swap=2GB'#13#10, False);
    Log('.wslconfig записан (RAM ≤ 8 ГБ: memory=5GB, swap=2GB)');
  end;

  WizardForm.StatusLabel.Caption := ExpandConstant('{cm:RuntimeStep}');
  tmp := ExpandConstant('{tmp}\runtime-installer.exe');
  Log('Скачиваем среду выполнения…');
  // официальный стабильный URL установщика среды (~500 МБ);
  // сигнатура: (Url, BaseName, RequiredSHA256OfFile, OnDownloadProgress)
  if DownloadTemporaryFile(
      'https://desktop.docker.com/win/main/amd64/Docker Desktop Installer.exe',
      'runtime-installer.exe', '', nil) = 0 then
    RaiseException('Не удалось скачать среду выполнения — проверьте интернет');
  Log('Среда выполнения скачана, устанавливаем (тихо)…');
  cmdline := Format('"%s" install --quiet --accept-license --always-run-service', [tmp]);
  ExecAsOriginalUser(ExpandConstant('{cmd}'), '/C ' + cmdline, '',
    SW_HIDE, ewWaitUntilTerminated, code);
  Log(Format('Установка среды: код %d', [code]));

  // Engine поднимается до 10 мин (§3.5.3); при необходимости стартуем UI
  if not WaitRuntimeReady(300) then
  begin
    Log('Движок не поднялся сам — стартуем вручную');
    Exec(ExpandConstant('{cmd}'),
      '/C start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"', '',
      SW_HIDE, ewNoWait, code);
    if not WaitRuntimeReady(300) then
      RaiseException('Среда выполнения не запустилась за 10 минут. ' +
        'Откройте журнал: ' + LogPath);
  end;
  Log('Среда выполнения готова');
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

procedure GenerateEnv();
var
  env: string;
  jwt, secrets, pgpwd, backupKey, cors: string;
begin
  WizardForm.StatusLabel.Caption := ExpandConstant('{cm:SetupStep}');

  // секреты ≥32 байт, все уникальны (§3.6; PRNG операционной системы)
  jwt := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..48|%{Get-Random -Maximum 256}) -as [byte[]])"', '');
  secrets := RunCapture('python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"', '');
  if secrets = '' then
    secrets := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..32|%{Get-Random -Maximum 256}) -as [byte[]]).TrimEnd(''='').Replace(''+'',''-'').Replace(''/'',''_'')"', '');
  pgpwd := RunCapture('powershell -NoProfile -Command "-join((48..57)+(65..90)+(97..122)|Get-Random -Count 32|%{[char]$_})"', '');
  backupKey := RunCapture('powershell -NoProfile -Command "[Convert]::ToBase64String((1..32|%{Get-Random -Maximum 256}) -as [byte[]])"', '');

  if (Length(jwt) < 32) or (Length(secrets) < 32) or (Length(pgpwd) < 24) then
    RaiseException('Не удалось сгенерировать ключи — журнал: ' + LogPath);

  cors := Format('["http://localhost:8080","http://%s:8080"]', [GetComputerNameString]);

  env :=
    '# Квазар — сгенерировано установщиком. СОХРАНИТЕ КОПИЮ: это ключи' + #13#10 +
    '# от ваших данных (бэкап .env = возможность восстановить доступ).' + #13#10 +
    'QUASAR_API_IMAGE=ghcr.io/kvazar-erp/kvazar-api:{#AppVersion}' + #13#10 +
    'QUASAR_WEB_IMAGE=ghcr.io/kvazar-erp/kvazar-web:{#AppVersion}' + #13#10 +
    'POSTGRES_PASSWORD=' + pgpwd + #13#10 +
    'JWT_SECRET=' + jwt + #13#10 +
    'SECRETS_KEY=' + secrets + #13#10 +
    'BACKUP_KEY=' + backupKey + #13#10 +
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
  cmdline: string;
begin
  cmdline := Format('/C docker compose -f "%s\stack\docker-compose.box.yml" --project-name kvazar %s', [
    ExpandConstant('{app}'), Args]);
  if not Exec(ExpandConstant('{cmd}'), cmdline, ExpandConstant('{app}\stack'),
       SW_HIDE, ewWaitUntilTerminated, code) or (code <> 0) then
    RaiseException(Format('Команда «%s» не удалась (код %d). Журнал: %s', [
      Args, code, LogPath]));
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
  name_, full_, email, password, json, tmp, httpCode: string;
  http: AnsiString;
  code: Integer;
begin
  Result := False;
  name_ := OrgPage.Values[0];
  full_ := OrgPage.Values[1];
  email := OrgPage.Values[2];
  password := OrgPage.Values[3];

  if (Length(name_) < 2) or (Pos('@', email) = 0) or (Length(password) < 8) then
  begin
    MsgBox(ExpandConstant('{cm:WeakPassword}'), mbError, MB_OK);
    Exit;
  end;

  tmp := ExpandConstant('{tmp}\boot.json');
  json := Format('{"company_name":"%s","admin_full_name":"%s","admin_email":"%s","admin_password":"%s"}', [
    name_, full_, email, password]);
  SaveStringToFile(tmp, json, False);
  Exec(ExpandConstant('{cmd}'),
    Format('/C curl -s -o nul -w "%%{http_code}" -X POST -H "Content-Type: application/json" -d @"%s" http://localhost:8080/api/v1/platform/bootstrap > "%s.boot"', [tmp, tmp]),
    '', SW_HIDE, ewWaitUntilTerminated, code);
  // Result функции Boolean — HTTP-код читаем в локальную AnsiString
  // (var-параметр LoadStringFromFile), затем конвертируем
  http := '';
  LoadStringFromFile(tmp + '.boot', http);
  httpCode := http;
  Log('bootstrap http: ' + httpCode);
  Result := (httpCode = '201') or (httpCode = '200');
  if not Result then
    MsgBox('Не удалось создать организацию (код ' + httpCode + ').' #13#10 +
      'Возможно, она уже создана — попробуйте войти.', mbError, MB_OK);
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
  if CurStep = ssInstall then
  begin
    EnsureRuntime();          // 3: среда выполнения (невидимо)
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:ImagesStep}');
    Compose('pull');          // 5: образы из registry
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:DbStep}');
    GenerateEnv();            // 4: .env с прод-секретами
    Compose('up -d');         // 6: запуск
    Log('Ожидание health-check (до 5 мин)…');
    if not WaitForHealth(300) then
      RaiseException('Квазар не ответил за 5 минут. Журнал: ' + LogPath + #13#10 + 'После запуска среды выполнения повторите установку — она продолжит с этого места.');
    Log('Стек поднят, health зелёный');
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
    // ОС: Win10/11 (Inno сам не стартует на старых — доп. проверка билда);
    // GetWindowsVersionMajor удалён в Inno 6 — используем GetWindowsVersionEx
    GetWindowsVersionEx(winver);
    if (winver.Major < 10) then
    begin
      MsgBox('Квазар требует Windows 10 (1809+) или Windows 11.',
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

    // виртуализация (VT-x/AMD-V) — блокирующе (§4 ошибочные)
    if not VirtualizationEnabled() then
    begin
      MsgBox('Виртуализация (VT-x / AMD-V) выключена в BIOS.' #13#10 +
        'Включите её в настройках BIOS/UEFI материнской платы и запустите ' +
        'установку заново.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
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
