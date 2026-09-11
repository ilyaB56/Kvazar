from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Custom ERP"
    database_url: str = "postgresql+psycopg2://erp:erp@localhost:5432/erp"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 60
    refresh_expire_days: int = 14

    # Пароль сид-админа. Дев-контур — дефолт; прод обязан задать SEED_ADMIN_PASSWORD
    # (security-plan P0-5 / реестр долгов №3).
    seed_admin_password: str = "admin12345"

    # Бэкапы и обновления (updates-and-backups-spec)
    backup_key: str = "KbPbCqtmutVQDaMHVvNSte7n0_F8VkalqpAkmWed5js="  # дев-ключ, ротация в проде
    backup_dir: str = "/backups"
    backup_retention: int = 3
    backup_schedule: str = "03:00"  # HH:MM, читается при старте beat
    update_manifest_url: str = "file:///app/deploy/test-manifest.json"
    update_channel: str = "stable"

    # ИИ-агент (ADR-006: v1 — только локальный Ollama; мок для тестов)
    ai_provider: str = "llm_mock"  # ollama | llm_mock
    ai_chat_model: str = "qwen2.5:7b-instruct"
    ai_embed_model: str = "bge-m3"
    ai_max_tool_steps: int = 5
    enable_external_llm: bool = False  # ADR-006: включение — отдельным решением

    # P1 security-plan п.7 (sales-automation §9.2): allowlist доменов
    # исходящих запросов коннекторов. Строгий режим (прод) — домен вне
    # списка запрещён; журнал каждого обращения — connectors/egress.py
    connector_allowlist: str = "yookassa.net,api.yookassa.ru,securepay.tinkoff.ru,api.telegram.org,localhost,127.0.0.1,host.docker.internal"
    connector_allowlist_strict: bool = False  # прод: True

    # Ключ для шифрования секретов подключений (Fernet). В проде — из секрет-хранилища.
    secrets_key: str = "change-me-fernet-key"

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
