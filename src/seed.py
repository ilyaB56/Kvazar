"""Первичный сид: админ и запись модулей в registry. Идемпотентный."""

from sqlalchemy import select

from src.config import get_settings
from src.core.auth import hash_password
from src.core.models import ModuleRegistry, User
from src.core.plugins import MANIFESTS
from src.db import SessionLocal


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
        db.commit()
        print("seed ok: admin@example.com "
              "(пароль: SEED_ADMIN_PASSWORD, дев-дефолт admin12345 — смените!)")
    finally:
        db.close()


if __name__ == "__main__":
    run()
