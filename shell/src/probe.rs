//! Health-зонд (tauri-shell-spec §5.4): комбинированная проверка
//! `/health` + зонд принадлежности `/api/v1/modules` (защита от чужого
//! сервиса на том же порту — инцидент 2026-10-03).

use serde::Serialize;
use std::time::Duration;

pub const TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ProbeResult {
    /// Сервер жив и это Квазар
    Ok,
    /// Что-то отвечает, но это не похоже на Квазар (жёлтое предупреждение)
    NotKvazar,
    /// Не отвечает / health не ок
    Unreachable,
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(TIMEOUT)
        .user_agent(concat!("KvazarShell/", env!("CARGO_PKG_VERSION"), " (probe)"))
        .build()
        .expect("reqwest client")
}

/// 1. `GET /health` → 200 + `"status":"ok"`; 2. `GET /api/v1/modules` → 200
/// и JSON-массив с полями name/version/db_schema.
pub async fn probe(base_url: &str) -> ProbeResult {
    let client = client();
    // шаг 1: health
    let health = match client.get(format!("{base_url}/health")).send().await {
        Ok(r) => match r.status().as_u16() {
            200 => r.json::<serde_json::Value>().await,
            _ => return ProbeResult::Unreachable,
        },
        Err(_) => return ProbeResult::Unreachable,
    };
    let ok = matches!(&health, Ok(v) if v.get("status").and_then(|s| s.as_str()) == Some("ok"));
    if !ok {
        return ProbeResult::Unreachable;
    }
    // шаг 2: зонд принадлежности
    match client
        .get(format!("{base_url}/api/v1/modules"))
        .send()
        .await
    {
        Ok(r) if r.status().as_u16() == 200 => match r.json::<serde_json::Value>().await {
            Ok(serde_json::Value::Array(items)) => {
                let looks_like_kvazar = items.iter().any(|it| {
                    it.get("name").is_some()
                        && it.get("version").is_some()
                        && it.get("db_schema").is_some()
                });
                if looks_like_kvazar {
                    ProbeResult::Ok
                } else {
                    ProbeResult::NotKvazar
                }
            }
            _ => ProbeResult::NotKvazar,
        },
        _ => ProbeResult::NotKvazar,
    }
}
