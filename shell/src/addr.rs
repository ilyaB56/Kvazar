//! Валидация и нормализация адреса сервера (tauri-shell-spec §5.2).

/// Обрезка пробелов, авто-схема http://, только http(s), хост обязателен,
/// максимум 2000 символов, канонизация до origin+порта (путь отсекается).
pub fn normalize(input: &str) -> Result<String, String> {
    let mut s = input.trim().trim_end_matches('/').to_string();
    if s.is_empty() {
        return Err("Введите адрес сервера".into());
    }
    if s.len() > 2000 {
        return Err("Адрес слишком длинный".into());
    }
    // Нет схемы (нет "://") → добавляем http://
    if !s.contains("://") {
        s = format!("http://{s}");
    }
    let url = url::Url::parse(&s).map_err(|_| "Не удалось разобрать адрес".to_string())?;
    match url.scheme() {
        "http" | "https" => {}
        _ => return Err("Поддерживаются только адреса http(s)".into()),
    }
    let host = url.host_str().filter(|h| !h.is_empty());
    if host.is_none() {
        return Err("В адресе нет имени сервера".into());
    }
    let mut out = format!("{}://{}", url.scheme(), host.unwrap());
    if let Some(port) = url.port() {
        out.push_str(&format!(":{port}"));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds_http_and_normalizes() {
        assert_eq!(normalize("192.168.1.10:8080").unwrap(), "http://192.168.1.10:8080");
        assert_eq!(normalize(" http://localhost:8080/ ").unwrap(), "http://localhost:8080");
        assert_eq!(normalize("https://x.kvazar.ru").unwrap(), "https://x.kvazar.ru");
    }

    #[test]
    fn rejects_bad_schemes_and_text() {
        assert!(normalize("file:///C:/").is_err());
        assert!(normalize("ftp://x").is_err());
        assert!(normalize("javascript:alert(1)").is_err());
        assert!(normalize("ноутбук Васи").is_err());
        assert!(normalize("").is_err());
    }
}
