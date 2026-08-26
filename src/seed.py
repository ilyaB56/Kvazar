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
        db.commit()
        print("seed ok: admin@example.com "
              "(пароль: SEED_ADMIN_PASSWORD, дев-дефолт admin12345 — смените!); "
              "цепочка: ЦБ РФ + «Курсы ЦБ» (30 0 * * *)")
    finally:
        db.close()


if __name__ == "__main__":
    run()
