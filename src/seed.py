"""Первичный сид: админ, реестр модулей, витринная цепочка. Идемпотентный."""

from sqlalchemy import select

from src.config import get_settings
from src.core.auth import hash_password
from src.core.models import ModuleRegistry, User
from src.core.plugins import MANIFESTS
from src.db import SessionLocal
from src.modules.integrations import models as im


def _seed_cbr_chain(db) -> None:
    """Витринная цепочка (showcase-chain, этап C): курсы ЦБ РФ по расписанию."""
    connection = db.scalar(select(im.Connection).where(im.Connection.name == "ЦБ РФ"))
    if connection is None:
        connection = im.Connection(
            name="ЦБ РФ",
            connector_code="cbr",
            credentials_enc="",
            config={"base_url": "https://www.cbr.ru"},
        )
        db.add(connection)
        db.flush()
    job = db.scalar(select(im.SyncJob).where(im.SyncJob.name == "Курсы ЦБ"))
    if job is None:
        db.add(im.SyncJob(
            name="Курсы ЦБ",
            connection_id=connection.id,
            direction="fetch",
            cron="30 0 * * *",
            endpoint="",
            emit_event="integration.rates.fetched",
        ))


def _seed_ai_chain(db) -> None:
    """ИИ-агент (ai-agent-spec, этап A): connection ollama + токен агента.

    Токен ai-agent (роль user, ADR-006 п.4 — наименьшие привилегии) выдаётся
    один раз и кладётся в connection self-api: кредлы шифруются Fernet.
    """
    import hashlib
    import secrets as py_secrets

    from src.core.models import ApiToken

    connection = db.scalar(select(im.Connection).where(im.Connection.name == "ollama"))
    if connection is None:
        db.add(im.Connection(
            name="ollama",
            connector_code="ollama",
            credentials_enc="",
            config={"base_url": "http://ollama:11434", "timeout_seconds": 300},
        ))

    if db.scalar(select(ApiToken).where(ApiToken.name == "ai-agent")) is None:
        from src.modules.integrations.crypto import encrypt_dict

        token = py_secrets.token_urlsafe(32)
        db.add(ApiToken(
            name="ai-agent",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            role="user",
        ))
        # self-api connection: инструменты агента ходят в публичный API
        # с X-API-Token (механика витринной цепочки); кредлы — Fernet
        db.add(im.Connection(
            name="ai-self-api",
            connector_code="http_rest",
            credentials_enc=encrypt_dict({"api_key": token}),
            config={"base_url": "http://api:8000", "auth_style": "header",
                    "auth_header_name": "X-API-Token", "timeout_seconds": 30},
        ))


def run() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == "admin@example.com")) is None:
            db.add(User(
                email="admin@example.com",
                password_hash=hash_password(get_settings().seed_admin_password),
                full_name="Administrator",
                role="admin",
            ))
        for m in MANIFESTS:
            row = db.scalar(select(ModuleRegistry).where(ModuleRegistry.name == m.name))
            if row is None:
                db.add(ModuleRegistry(
                    name=m.name, version=m.version, db_schema=m.db_schema,
                    depends_on=list(m.depends_on),
                ))
            else:
                row.version = m.version
        _seed_cbr_chain(db)
        _seed_ai_chain(db)
        db.commit()
        print("seed ok: admin@example.com "
              "(пароль: SEED_ADMIN_PASSWORD, дев-дефолт admin12345 — смените!); "
              "цепочка: ЦБ РФ + «Курсы ЦБ» (30 0 * * *); ИИ: ollama + токен ai-agent")
    finally:
        db.close()


if __name__ == "__main__":
    run()
