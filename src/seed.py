"""Первичный сид: админ, реестр модулей, витринная цепочка. Идемпотентный."""

from datetime import date

from sqlalchemy import select

from src.config import get_settings
from src.core.auth import hash_password
from src.core.models import Company, ModuleRegistry, User
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

    # токен, созданный до введения owner_user_id, привязываем к админу
    orphan = db.scalar(select(ApiToken).where(ApiToken.name == "ai-agent"))
    if orphan is not None and orphan.owner_user_id is None:
        admin = db.scalar(select(User).where(User.role == "admin"))
        if admin is not None:
            orphan.owner_user_id = admin.id

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


def ensure_ai_self_api(db, company_id, admin_user_id=None) -> None:
    """Connection «ai-self-api» для инструментов ИИ (tools.py): http_rest
    на собственный API с X-API-Token (role user, контекст организации).

    Идемпотентно: существует активный ai-self-api — ничего не делаем.
    Вызывается при создании организации (платформа/одобрение signup) и
    на bootstrap для существующих организаций без него."""
    import hashlib
    import secrets as _secrets

    from sqlalchemy import select as _select

    from src.modules.integrations import models as im
    from src.modules.integrations.crypto import decrypt_dict, encrypt_dict

    existing = db.scalar(_select(im.Connection).where(
        im.Connection.name == "ai-self-api",
        im.Connection.company_id == company_id,
        im.Connection.is_active.is_(True)))
    if existing is not None:
        # самолечение старых сидов: токен без владельца даёт ApiPrincipal.id
        # = id токена → FK users при created_by (баг этапа B); восстанавливаем
        from src.core.models import ApiToken, User as _User

        owner_id = admin_user_id
        if owner_id is None:
            owner = db.scalar(_select(_User).where(
                _User.company_id == company_id,
                _User.role == "admin").order_by(_User.created_at))
            owner_id = owner.id if owner else None
        if owner_id is not None:
            import hashlib as _hl
            key = decrypt_dict(existing.credentials_enc).get("api_key", "")
            token = db.scalar(_select(ApiToken).where(
                ApiToken.token_hash == _hl.sha256(str(key).encode()).hexdigest()))
            if token is not None and token.owner_user_id is None:
                token.owner_user_id = owner_id
                db.flush()
        return
    token = _secrets.token_urlsafe(32)
    db.add(ApiToken(
        name="ai-self-api",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        role="user",
        owner_user_id=admin_user_id,
        company_id=company_id,
    ))
    db.add(im.Connection(
        company_id=company_id,
        name="ai-self-api",
        connector_code="http_rest",
        credentials_enc=encrypt_dict({"api_key": token}),
        # auth_style=header + X-API-Token: http_rest с "none" не шлёт
        # токен вовсе (401); base с /api/v1 — tools.py нормализует сам
        config={"base_url": "http://api:8000/api/v1",
                "auth_style": "header", "auth_header_name": "X-API-Token"},
    ))
    db.flush()


def seed_company_data(db, company_id) -> None:
    """Стартовые данные организации (multitenancy §5.1): базовые категории,
    склады + системные транзиты, период текущего месяца. Вызывается ядром
    при создании организации; модули не тянутся напрямую (агрегатор — ядро).
    Стадии CRM — свои таблицы (этап B2 добавит их сюда же)."""
    from src.modules.mgmt_accounting import models as acc_m
    from src.modules.mgmt_accounting.features.inventory import models as inv_m

    for name, kind in (("Продажи", "income"), ("Закупки товаров", "expense")):
        if db.scalar(select(acc_m.Category).where(
                acc_m.Category.name == name, acc_m.Category.kind == kind,
                acc_m.Category.company_id == company_id)) is None:
            db.add(acc_m.Category(name=name, kind=kind, company_id=company_id))
    for name, kind, transit in (
            ("Основной склад", "physical", False),
            ("Цифровой склад", "digital", False),
            ("Поставщик", "physical", True),
            ("Клиент", "physical", True),
            ("Производство", "physical", True),
            ("Брак", "physical", True)):
        if db.scalar(select(inv_m.Location).where(
                inv_m.Location.name == name,
                inv_m.Location.company_id == company_id)) is None:
            db.add(inv_m.Location(name=name, kind=kind, is_transit=transit,
                                  company_id=company_id))
    # стадии CRM — копией эталона «Основной» (позиции/вероятности)
    from src.modules.mini_crm import models as crm_m

    has_stages = db.scalar(select(crm_m.Stage.id).where(
        crm_m.Stage.company_id == company_id).limit(1))
    if has_stages is None:
        main = db.scalar(select(Company).where(Company.name == "Основная"))
        if main is not None and main.id != company_id:
            for row in db.scalars(select(crm_m.Stage).where(
                    crm_m.Stage.company_id == main.id).order_by(crm_m.Stage.position)).all():
                db.add(crm_m.Stage(
                    company_id=company_id, name=row.name, position=row.position,
                    probability=row.probability, is_won=row.is_won,
                    is_lost=row.is_lost, is_active=row.is_active))
    today = date.today()
    db.add(acc_m.Period(year=today.year, month=today.month, status="open",
                        company_id=company_id))


def run() -> None:
    db = SessionLocal()
    try:
        # bootstrap (самообслуживание/ИИ, 2026-09-22): существующим
        # организациям без ai-self-api — создать (роль user, владелец —
        # старейший админ организации)
        try:
            for company in db.scalars(select(Company)).all():
                owner = db.scalar(select(User).where(
                    User.company_id == company.id, User.role == "admin"
                ).order_by(User.created_at))
                ensure_ai_self_api(db, company.id,
                                   admin_user_id=owner.id if owner else None)
            db.commit()
        except Exception:  # noqa: BLE001 — сид не должен валить старт
            db.rollback()
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
