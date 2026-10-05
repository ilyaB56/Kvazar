//! Оболочка «Квазар» (tauri-shell-spec, этапы A+B).
//!
//! Машина состояний (§6): Первый запуск → Подключаемся → Готово(webview) /
//! Квазар не запущен. Оболочка — обёртка над существующим веб-интерфейсом:
//! loadUrl настраиваемого server_url, никакой бизнес-логики ERP.
//!
//! Этап B (§15-B): настройки (§7/§9), автозапуск (§7.5), close-to-tray
//! с первым диалогом (§7.3), нативные тосты + polling-заготовка (§8),
//! «Проверить обновления…» (§12.4).

mod addr;
mod config;
mod probe;

use config::ShellConfig;
use probe::ProbeResult;
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewWindow,
};

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Экран настройки адреса/настроек (первый запуск / «Настройки»)
    Setup,
    /// «Подключаемся к {адрес}…»
    Connecting,
    /// Webview с интерфейсом ERP
    Ready,
    /// Экран-заглушка «Квазар не запущен»
    Unavailable,
}

struct Shell {
    phase: Mutex<Phase>,
    /// origin локальных экранов (http://tauri.localhost на Windows) —
    /// вычисляется один раз при старте, пока окно на локальном экране.
    local_origin: String,
    config: Mutex<ShellConfig>,
}

impl Shell {
    fn current_phase(&self) -> Phase {
        self.phase.lock().map(|p| p.clone()).unwrap_or(Phase::Setup)
    }

    fn with_config<T>(&self, f: impl FnOnce(&mut ShellConfig) -> T) -> Option<T> {
        let mut guard = self.config.lock().ok()?;
        Some(f(&mut guard))
    }
}

const CONNECT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
const CONNECT_POLL: std::time::Duration = std::time::Duration::from_secs(2);
const READY_MONITOR: std::time::Duration = std::time::Duration::from_secs(30);
/// §8.3: интервал polling уведомлений — 60 с (выровнен с клиентским
/// центром уведомлений), бэкофф до 300 с, feature-detect-пауза 1 ч.
const NOTIFY_POLL: std::time::Duration = std::time::Duration::from_secs(60);
const NOTIFY_BACKOFF: std::time::Duration = std::time::Duration::from_secs(300);
const NOTIFY_FEATURE_PAUSE: std::time::Duration = std::time::Duration::from_secs(3600);

/// Страница релизов визитки (§12.4, О1: рекомендация (б) — без updater).
const RELEASES_URL: &str = "https://github.com/ilyaB56/kvazar-download/releases";

fn eval_goto(app: &tauri::AppHandle, url: &str) {
    if let Some(win) = app.get_webview_window("main") {
        // экранируем одинарные кавычки (адрес после normalize их не
        // содержит, но defensively)
        let safe = url.replace('\'', "%27");
        let _ = win.eval(&format!("location.href = '{safe}'"));
    }
}

fn show_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// Смена состояния: обновить phase, перейти на соответствующий экран и
/// запустить его цикл зондирования (предыдущие циклы завершаются,
/// обнаружив смену состояния).
fn set_phase(app: &tauri::AppHandle, phase: Phase) {
    {
        let shell = app.state::<Shell>();
        let guard = shell.phase.lock();
        if let Ok(mut p) = guard {
            *p = phase.clone();
        }
    }
    let local = app.state::<Shell>().local_origin.clone();
    match phase {
        Phase::Setup => eval_goto(app, &format!("{local}/index.html")),
        Phase::Connecting => {
            eval_goto(app, &format!("{local}/connecting.html"));
            let app2 = app.clone();
            tauri::async_runtime::spawn(connect_loop(app2));
        }
        Phase::Unavailable => {
            eval_goto(app, &format!("{local}/unavailable.html"));
            let app2 = app.clone();
            tauri::async_runtime::spawn(retry_loop(app2));
        }
        Phase::Ready => {
            let url = app
                .state::<Shell>()
                .config
                .lock()
                .ok()
                .and_then(|c| c.server_url.clone())
                .unwrap_or_default();
            if url.is_empty() {
                set_phase(app, Phase::Setup);
                return;
            }
            // §7.1: признак для точечной адаптации фронта (§13)
            eval_goto(app, &format!("{url}/?shell=tauri"));
            let app2 = app.clone();
            tauri::async_runtime::spawn(ready_monitor(app2));
            let app3 = app.clone();
            tauri::async_runtime::spawn(notification_poll(app3));
        }
    }
}

/// [Подключаемся]: зонд раз в 2 с; успех → Готово; 10 с безуспешности →
/// [Не запущен] (автоповтор продолжает работать там).
async fn connect_loop(app: tauri::AppHandle) {
    let url = match app.state::<Shell>().config.lock().ok().and_then(|c| c.server_url.clone()) {
        Some(u) => u,
        None => return,
    };
    let started = std::time::Instant::now();
    loop {
        if app.state::<Shell>().current_phase() != Phase::Connecting {
            return;
        }
        match probe::probe(&url).await {
            ProbeResult::Ok | ProbeResult::NotKvazar => {
                // NotKvazar при автоподключении к сохранённому адресу не
                // блокирует (предупреждение показывается только на экране
                // настройки, §5.4); интерпретация — в commit message.
                set_phase(&app, Phase::Ready);
                return;
            }
            ProbeResult::Unreachable => {}
        }
        if started.elapsed() >= CONNECT_TIMEOUT {
            set_phase(&app, Phase::Unavailable);
            return;
        }
        tokio::time::sleep(CONNECT_POLL).await;
    }
}

/// [Не запущен]: автоповтор зонда каждые 10 с с бэкоффом до 60 с.
async fn retry_loop(app: tauri::AppHandle) {
    let url = match app.state::<Shell>().config.lock().ok().and_then(|c| c.server_url.clone()) {
        Some(u) => u,
        None => return,
    };
    let mut interval = std::time::Duration::from_secs(10);
    loop {
        tokio::time::sleep(interval).await;
        if app.state::<Shell>().current_phase() != Phase::Unavailable {
            return;
        }
        if matches!(probe::probe(&url).await, ProbeResult::Ok | ProbeResult::NotKvazar) {
            set_phase(&app, Phase::Connecting);
            return;
        }
        interval = (interval * 2).min(std::time::Duration::from_secs(60));
    }
}

/// [Готово]: падение сервера при загруженном интерфейсе — показать
/// [Не запущен] поверх (критерий приёмки A.2). Лёгкий монитор раз в 30 с;
/// интерпретация «фонового мониторинга» из §8.3 — в commit message.
async fn ready_monitor(app: tauri::AppHandle) {
    let url = match app.state::<Shell>().config.lock().ok().and_then(|c| c.server_url.clone()) {
        Some(u) => u,
        None => return,
    };
    loop {
        tokio::time::sleep(READY_MONITOR).await;
        if app.state::<Shell>().current_phase() != Phase::Ready {
            return;
        }
        if matches!(probe::probe(&url).await, ProbeResult::Unreachable) {
            set_phase(&app, Phase::Unavailable);
            return;
        }
    }
}

// ---------- Нативные уведомления (§8, этап B) ----------

fn notify(app: &tauri::AppHandle, body: &str) {
    use tauri_plugin_notification::NotificationExt;
    let _ = app
        .notification()
        .builder()
        .title("Квазар")
        .body(body)
        .show();
}

/// Извлечение счётчика непрочитанных из ответа эндпоинта: поле пока не
/// финализировано серверной спекой (§8.3) — принимаем типовые варианты.
fn extract_unread(v: &serde_json::Value) -> Option<u64> {
    v.get("unread_count")
        .or_else(|| v.get("count"))
        .or_else(|| v.get("unread"))
        .and_then(|x| x.as_u64())
}

/// Polling-заготовка по контракту §8.3 (задание этапа B): раз в 60 с
/// ТОЛЬКО в фазе Ready, анонимно (оболочка не хранит токены, §11 —
/// авторизованный poll выполняет вебвю после серверной спеки, О9).
/// 401/403/404/501 → эндпоинта нет/нет прав — тишина и пауза 1 ч;
/// недоступен → бэкофф 300 с. Новый непрочитанный (> прошлого) → тост
/// «Квазар / Новых уведомлений: N».
async fn notification_poll(app: tauri::AppHandle) {
    let url = match app.state::<Shell>().config.lock().ok().and_then(|c| c.server_url.clone()) {
        Some(u) => u,
        None => return,
    };
    let client = match reqwest::Client::builder()
        .timeout(probe::TIMEOUT)
        .user_agent(concat!("KvazarShell/", env!("CARGO_PKG_VERSION"), " (notifications)"))
        .build()
    {
        Ok(c) => c,
        Err(_) => return,
    };
    let mut interval = NOTIFY_POLL;
    let mut prev: Option<u64> = None;
    loop {
        tokio::time::sleep(interval).await;
        if app.state::<Shell>().current_phase() != Phase::Ready {
            return;
        }
        // §8.3: при выключенных уведомлениях — никакой фоновой активности
        if !app
            .state::<Shell>()
            .config
            .lock()
            .map(|c| c.notifications_enabled)
            .unwrap_or(false)
        {
            prev = None;
            continue;
        }
        let resp = client
            .get(format!("{url}/api/v1/notifications/unread-count"))
            .send()
            .await;
        match resp {
            Ok(r) => match r.status().as_u16() {
                200 => {
                    interval = NOTIFY_POLL;
                    if let Ok(v) = r.json::<serde_json::Value>().await {
                        if let Some(n) = extract_unread(&v) {
                            // тост только о ПРИРОСТЕ: новый непрочитанный
                            // строго больше прошлого известного значения
                            if let Some(p) = prev {
                                if n > p {
                                    notify(&app, &format!("Новых уведомлений: {}", n - p));
                                }
                            }
                            prev = Some(n);
                        } else {
                            prev = None;
                        }
                    }
                }
                // feature-detect: эндпоинта нет (404/501) или требуется
                // авторизация (401/403) — режим недоступен, тишина (§8.2)
                401 | 403 | 404 | 501 => {
                    interval = NOTIFY_FEATURE_PAUSE;
                    prev = None;
                }
                _ => interval = NOTIFY_BACKOFF,
            },
            Err(_) => interval = NOTIFY_BACKOFF,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::extract_unread;

    #[test]
    fn unread_extracted_from_typical_fields() {
        assert_eq!(
            extract_unread(&serde_json::json!({"unread_count": 3})),
            Some(3)
        );
        assert_eq!(extract_unread(&serde_json::json!({"count": 7})), Some(7));
        assert_eq!(extract_unread(&serde_json::json!({"unread": 0})), Some(0));
        assert_eq!(extract_unread(&serde_json::json!({"detail": "x"})), None);
        assert_eq!(extract_unread(&serde_json::json!("нет")), None);
    }
}

// ---------- IPC-команды локальных экранов ----------

#[derive(serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnectOutcome {
    Connected,
    NotKvazar,
    SavedNoCheck,
}

#[tauri::command]
fn shell_state(app: tauri::AppHandle) -> serde_json::Value {
    let shell = app.state::<Shell>();
    serde_json::json!({
        "phase": shell.current_phase(),
        "server_url": shell.config.lock().ok().and_then(|c| c.server_url.clone()),
    })
}

/// Состояние экрана настроек (§7/§9): адрес, список последних, опции,
/// версия для «О программе» (§15-C п.19 — выводим сразу, это одна строка).
#[tauri::command]
fn settings_state(app: tauri::AppHandle) -> serde_json::Value {
    let shell = app.state::<Shell>();
    let cfg = shell.config.lock().map(|c| c.clone()).unwrap_or_default();
    serde_json::json!({
        "server_url": cfg.server_url,
        "recent_addresses": cfg.recent_addresses,
        "close_to_tray": cfg.close_to_tray,
        "autostart": cfg.autostart,
        "notifications_enabled": cfg.notifications_enabled,
        "version": env!("CARGO_PKG_VERSION"),
    })
}

/// Смена опции из настроек. autostart — через плагин (§7.5), фактическое
/// состояние ключа HKCU Run важнее флага конфига.
#[tauri::command]
fn set_setting(
    app: tauri::AppHandle,
    key: String,
    value: bool,
) -> Result<(), String> {
    let shell = app.state::<Shell>();
    match key.as_str() {
        "close_to_tray" => {
            shell
                .with_config(|c| {
                    c.close_to_tray = value;
                    // явное изменение опции = осознанный выбор (диалог
                    // закрытия больше не нужен)
                    c.close_to_tray_decided = true;
                })
                .ok_or("конфиг недоступен")?;
        }
        "notifications_enabled" => {
            shell
                .with_config(|c| c.notifications_enabled = value)
                .ok_or("конфиг недоступен")?;
        }
        "autostart" => {
            use tauri_plugin_autostart::ManagerExt;
            let enabled = if value {
                app.autolaunch()
                    .enable()
                    .map_err(|e| format!("не удалось включить автозапуск: {e}"))?;
                true
            } else {
                let _ = app.autolaunch().disable();
                false
            };
            shell
                .with_config(|c| c.autostart = enabled)
                .ok_or("конфиг недоступен")?;
        }
        _ => return Err(format!("неизвестная опция: {key}")),
    }
    let cfg = shell.config.lock().map(|c| c.clone()).unwrap_or_default();
    config::save(&cfg);
    Ok(())
}

/// Тестовое уведомление (§8.2): приёмка без серверной части.
#[tauri::command]
fn test_notification(app: tauri::AppHandle) {
    notify(&app, "Тестовое уведомление оболочки Квазара");
}

/// «Проверить и подключиться» (§5.4): валидация → зонд → сохранение.
#[tauri::command]
async fn connect(app: tauri::AppHandle, address: String) -> Result<ConnectOutcome, String> {
    let normalized = addr::normalize(&address)?;
    match probe::probe(&normalized).await {
        ProbeResult::Ok => {
            save_url(&app, &normalized);
            set_phase(&app, Phase::Ready);
            Ok(ConnectOutcome::Connected)
        }
        ProbeResult::NotKvazar => Ok(ConnectOutcome::NotKvazar),
        ProbeResult::Unreachable => Err("Сервер по этому адресу не отвечает".into()),
    }
}

/// «Сохранить без проверки» и «Продолжить всё равно» (осознанный обход).
#[tauri::command]
async fn connect_without_check(app: tauri::AppHandle, address: String) -> Result<ConnectOutcome, String> {
    let normalized = addr::normalize(&address)?;
    save_url(&app, &normalized);
    set_phase(&app, Phase::Connecting);
    Ok(ConnectOutcome::SavedNoCheck)
}

/// «Проверить снова» на экране-заглушке.
#[tauri::command]
fn retry_now(app: tauri::AppHandle) {
    if app.state::<Shell>().current_phase() == Phase::Unavailable {
        set_phase(&app, Phase::Connecting);
    }
}

/// «Настройки» / смена адреса (§5.3).
#[tauri::command]
fn open_settings(app: tauri::AppHandle) {
    set_phase(&app, Phase::Setup);
}

/// Ответ первого диалога закрытия (§7.3): "exit" | "tray".
#[tauri::command]
fn close_dialog_answer(app: tauri::AppHandle, choice: String, remember: bool) {
    if let Some(dlg) = app.get_webview_window("close-prompt") {
        let _ = dlg.close();
    }
    if remember {
        let shell = app.state::<Shell>();
        let tray = choice == "tray";
        let _ = shell.with_config(|c| {
            c.close_to_tray = tray;
            c.close_to_tray_decided = true;
        });
        let cfg = shell.config.lock().map(|c| c.clone()).unwrap_or_default();
        config::save(&cfg);
    }
    if choice == "tray" {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.hide();
        }
    } else {
        app.exit(0);
    }
}

fn save_url(app: &tauri::AppHandle, url: &str) {
    let shell = app.state::<Shell>();
    let guard = shell.config.lock();
    if let Ok(mut cfg) = guard {
        if cfg.server_url.as_deref() != Some(url) {
            // список последних адресов — до 5 (§5.1, этап B)
            cfg.recent_addresses = config::push_recent(cfg.recent_addresses.clone(), url);
            cfg.server_url = Some(url.to_string());
        }
        config::save(&cfg);
    }
}

// ---------- Закрытие окна → трей (§7.3) ----------

/// Первый диалог закрытия: маленькое локальное окно с чекбоксом
/// «Запомнить выбор» (паттерн Telegram). Отдельное окно, а не переход
/// главного: навигация главного экрана уничтожила бы состояние вебвю.
fn show_close_prompt(app: &tauri::AppHandle) {
    if let Some(dlg) = app.get_webview_window("close-prompt") {
        let _ = dlg.set_focus();
        return;
    }
    let _ = tauri::WebviewWindowBuilder::new(
        app,
        "close-prompt",
        tauri::WebviewUrl::App("close-prompt.html".into()),
    )
    .title("Квазар")
    .inner_size(440.0, 240.0)
    .resizable(false)
    .minimizable(false)
    .maximizable(false)
    .center()
    .build();
}

fn on_close_requested(app: &tauri::AppHandle) {
    let (close_to_tray, decided) = app
        .state::<Shell>()
        .config
        .lock()
        .map(|c| (c.close_to_tray, c.close_to_tray_decided))
        .unwrap_or((false, false));
    if close_to_tray {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.hide();
        }
    } else if !decided {
        show_close_prompt(app);
    } else {
        app.exit(0);
    }
}

// ---------- Трей ----------

fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Открыть Квазар", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Настройки…", true, None::<&str>)?;
    let updates = MenuItem::with_id(app, "updates", "Проверить обновления…", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &settings, &updates, &sep, &quit])?;
    // иконка трея — из окна (настраивается в tauri.conf.json); PNG-фолбэк
    // не нужен: у Image нет from_bytes/new_bytes в резолвящейся версии
    // tauri 2 (обе попытки — E0599 на CI), дефолтная иконка всегда есть
    let tray = TrayIconBuilder::new()
        .tooltip("Квазар")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .build(app)?;
    tray.on_menu_event(|app, event| match event.id().as_ref() {
        "open" => show_window(app),
        "settings" => {
            show_window(app);
            set_phase(app, Phase::Setup);
        }
        // §12.4 О1(б): без встроенного обновителя — открываем страницу
        // релизов визитки в системном браузере
        "updates" => {
            use tauri_plugin_opener::OpenerExt;
            let _ = app.opener().open_url(RELEASES_URL, None::<&str>);
        }
        "quit" => app.exit(0),
        _ => {}
    });
    tray.on_tray_icon_event(|tray, event| {
        if let TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        } = event
        {
            show_window(tray.app_handle());
        }
    });
    Ok(())
}

// ---------- Старт ----------

pub fn run() {
    tauri::Builder::default()
        // одноэкземплярность (§7.4): второй запуск фокусирует окно первого
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_window(app);
        }))
        // автозапуск (§7.5): ключ HKCU Run, per-user, без UAC
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            shell_state,
            settings_state,
            set_setting,
            test_notification,
            connect,
            connect_without_check,
            retry_now,
            open_settings,
            close_dialog_answer
        ])
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    on_close_requested(window.app_handle());
                }
            }
        })
        .setup(|app| {
            let cfg = config::load();
            let win: WebviewWindow = app.get_webview_window("main").expect("main window");
            let local_origin = win.url()?.origin().ascii_serialization();
            let has_url = cfg.server_url.is_some();
            app.manage(Shell {
                phase: Mutex::new(Phase::Setup),
                local_origin,
                config: Mutex::new(cfg),
            });
            build_tray(app.handle())?;
            // §6 СТАРТ: конфига нет → первый запуск (index.html уже
            // загружен окном по умолчанию); конфиг есть → Подключаемся
            if has_url {
                set_phase(app.handle(), Phase::Connecting);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("ошибка запуска оболочки Квазара");
}
