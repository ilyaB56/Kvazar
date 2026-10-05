//! Конфигурация оболочки (tauri-shell-spec §9).
//!
//! Файл: `%APPDATA%\Kvazar\shell\settings.json` (буквальный путь из спеки;
//! на не-Windows — каталог конфигов пользователя). Только нейтральные поля:
//! НИКОГДА токены/пароли/cookies (§11). При порче файла — сброс на первый
//! запуск, старый файл переименовывается в `.bak`.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

pub const SCHEMA_VERSION: i64 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShellConfig {
    #[serde(default)]
    pub server_url: Option<String>,
    #[serde(default)]
    pub close_to_tray: bool,
    #[serde(default)]
    pub autostart: bool,
    #[serde(default = "default_true")]
    pub notifications_enabled: bool,
    #[serde(default)]
    pub recent_addresses: Vec<String>,
    #[serde(default)]
    pub last_shown_notification_ids: Vec<String>,
    pub schema_version: i64,
}

fn default_true() -> bool {
    true
}

impl Default for ShellConfig {
    fn default() -> Self {
        Self {
            server_url: None,
            close_to_tray: false,
            autostart: false,
            notifications_enabled: true,
            recent_addresses: Vec::new(),
            last_shown_notification_ids: Vec::new(),
            schema_version: SCHEMA_VERSION,
        }
    }
}

/// Каталог конфига: %APPDATA%\Kvazar\shell (спека §9; on Windows APPDATA
/// указывает в Roaming). Fallback для других ОС — ~/.config/kvazar-shell.
pub fn config_path() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return PathBuf::from(appdata).join("Kvazar").join("shell");
        }
    }
    dirs_fallback()
}

#[cfg(not(windows))]
fn dirs_fallback() -> PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(".config")
        });
    base.join("kvazar-shell")
}

#[cfg(windows)]
fn dirs_fallback() -> PathBuf {
    PathBuf::from(".").join("kvazar-shell")
}

/// Загрузка с защитой от порчи: не-JSON → дефолт + `.bak` (§10, ошибочный
/// сценарий «конфиг повреждён» — сообщения об ошибке нет).
pub fn load() -> ShellConfig {
    let path = config_path().join("settings.json");
    match fs::read_to_string(&path) {
        Ok(text) => match serde_json::from_str::<ShellConfig>(&text) {
            Ok(cfg) => {
                // миграция по schema_version (пока версия одна)
                if cfg.schema_version > SCHEMA_VERSION {
                    return ShellConfig::default();
                }
                cfg
            }
            Err(_) => {
                let _ = fs::rename(&path, path.with_extension("json.bak"));
                ShellConfig::default()
            }
        },
        Err(_) => ShellConfig::default(),
    }
}

pub fn save(cfg: &ShellConfig) {
    let dir = config_path();
    let _ = fs::create_dir_all(&dir);
    if let Ok(text) = serde_json::to_string_pretty(cfg) {
        let _ = fs::write(dir.join("settings.json"), text);
    }
}
