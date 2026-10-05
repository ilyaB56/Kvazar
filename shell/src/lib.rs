//! Оболочка «Квазар» (tauri-shell-spec, этап A).
//!
//! Машина состояний (§6): Первый запуск → Подключаемся → Готово(webview) /
//! Квазар не запущен. Оболочка — обёртка над существующим веб-интерфейсом:
//! loadUrl настраиваемого server_url, никакой бизнес-логики ERP.

mod addr;
mod config;
mod probe;

use config::ShellConfig;
use probe::ProbeResult;
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewWindow,
};

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Экран настройки адреса (первый запуск / «Настройки»)
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
}

const CONNECT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
const CONNECT_POLL: std::time::Duration = std::time::Duration::from_secs(2);
const READY_MONITOR: std::time::Duration = std::time::Duration::from_secs(30);

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
        if let Ok(mut p) = shell.phase.lock() {
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

fn save_url(app: &tauri::AppHandle, url: &str) {
    let shell = app.state::<Shell>();
    if let Ok(mut cfg) = shell.config.lock() {
        if cfg.server_url.as_deref() != Some(url) {
            // список последних адресов — этап B (§5.1); здесь только
            // актуальный адрес
            cfg.server_url = Some(url.to_string());
        }
        config::save(&cfg);
    }
}

// ---------- Трей ----------

fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Открыть Квазар", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Настройки…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &settings, &quit])?;
    let icon = app.default_window_icon().cloned().unwrap_or_else(|| {
        tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png")).expect("icon")
    });
    let mut tray = TrayIconBuilder::new()
        .icon(icon)
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
        .invoke_handler(tauri::generate_handler![
            shell_state,
            connect,
            connect_without_check,
            retry_now,
            open_settings
        ])
        .setup(|app| {
            let cfg = config::load();
            let win: WebviewWindow = app.get_webview_window("main").expect("main window");
            let local_origin = win.url().origin().ascii_serialization();
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
