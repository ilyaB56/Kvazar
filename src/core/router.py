"""API ядра: auth, пользователи, компании, контакты, настройки, события."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core import events
from src.core.auth import (
    AdminUser,
    CurrentUser,
    WriteUser,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from src.core.models import AuditEvent, Backup, Company, Contact, EventOutbox, RevokedToken, Setting, User
from src.core.passwords import validate_password
from src.core.rate_limit import check_login_rate_limit, reset_login_rate_limit
from src.db import SessionLocal, get_db

router = APIRouter(tags=["core"])


# ---------- Schemas ----------

class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "user"

    @field_validator("password")
    @classmethod
    def check_password_policy(cls, value: str) -> str:
        violations = validate_password(value)
        if violations:
            raise ValueError("; ".join(violations))
        return value


class CompanyIn(BaseModel):
    name: str
    inn: str = ""


class ContactIn(BaseModel):
    company_id: uuid.UUID | None = None
    full_name: str
    email: str = ""
    phone: str = ""


class SettingIn(BaseModel):
    key: str
    value: str | int | bool | dict
    value_type: str = "string"


# ---------- Auth ----------

@router.post("/auth/login", response_model=TokenOut)
def login(request: Request, body: LoginIn, db: Session = Depends(get_db)):
    check_login_rate_limit(request)
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Bad credentials")
    reset_login_rate_limit(request)
    db.add(
        AuditEvent(user_id=user.id, action="login", entity_type="user", entity_id=str(user.id))
    )
    db.commit()
    return TokenOut(
        access_token=create_access_token(user.id, user.role, ver=user.token_version),
        refresh_token=create_refresh_token(user.id, ver=user.token_version),
    )


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    from jwt import PyJWTError

    try:
        payload = decode_token(body.refresh_token)
    except PyJWTError as exc:
        raise HTTPException(401, "Invalid refresh token") from exc
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Wrong token type")
    if payload.get("jti") and db.get(RevokedToken, uuid.UUID(payload["jti"])) is not None:
        raise HTTPException(401, "Token revoked")
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(401, "User not found")
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(401, "Token revoked")
    return TokenOut(
        access_token=create_access_token(user.id, user.role, ver=user.token_version),
        refresh_token=create_refresh_token(user.id, ver=user.token_version),
    )


@router.post("/auth/logout")
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    """Отзыв refresh-токена (jti → blacklist до его exp). Access-токен живёт
    свои минуты и умирает сам — задокументировано в README."""
    from jwt import PyJWTError

    try:
        payload = decode_token(body.refresh_token)
    except PyJWTError as exc:
        raise HTTPException(401, "Invalid refresh token") from exc
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Wrong token type")
    user = db.get(User, payload["sub"])
    if user is None or payload.get("ver", 0) != user.token_version:
        raise HTTPException(401, "Token revoked")
    jti = payload.get("jti")
    if jti:
        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
        if db.get(RevokedToken, uuid.UUID(jti)) is None:
            db.add(RevokedToken(
                jti=uuid.UUID(jti), user_id=user.id, expires_at=expires_at
            ))
    db.add(AuditEvent(user_id=user.id, action="logout", entity_type="user", entity_id=str(user.id)))
    db.commit()
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn, user: CurrentUser, db: Session = Depends(get_db)):
    """Смена своего пароля: token_version += 1 — все сессии пользователя умирают,
    требуется повторный вход. Аудит password.changed."""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(403, "Wrong old password")
    violations = validate_password(body.new_password)
    if violations:
        raise HTTPException(422, "; ".join(violations))
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.add(AuditEvent(
        user_id=user.id, action="password.changed", entity_type="user", entity_id=str(user.id)
    ))
    db.commit()
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


# ---------- Users (admin) ----------

@router.get("/users", response_model=list[UserOut])
def list_users(admin: AdminUser, db: Session = Depends(get_db)):
    return db.scalars(select(User)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: AdminUser, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email already exists")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.add(AuditEvent(action="user.created", entity_type="user", payload={"email": body.email}))
    db.commit()
    db.refresh(user)
    return user


# ---------- Companies / Contacts ----------

@router.get("/companies", response_model=list[CompanyIn])
def list_companies(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Company)).all()


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, user: WriteUser, db: Session = Depends(get_db)):
    company = Company(name=body.name, inn=body.inn)
    db.add(company)
    events.publish(db, "company.created", {"name": body.name, "inn": body.inn})
    db.commit()
    db.refresh(company)
    return company


@router.get("/contacts")
def list_contacts(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Contact)).all()


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, user: WriteUser, db: Session = Depends(get_db)):
    contact = Contact(**body.model_dump())
    db.add(contact)
    events.publish(db, "contact.created", body.model_dump(mode="json"))
    db.commit()
    db.refresh(contact)
    return contact


# ---------- Settings ----------

@router.get("/settings")
def list_settings(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Setting)).all()


@router.put("/settings")
def upsert_setting(body: SettingIn, admin: AdminUser, db: Session = Depends(get_db)):
    setting = db.scalar(select(Setting).where(Setting.key == body.key))
    if setting:
        setting.value = body.value
        setting.value_type = body.value_type
    else:
        db.add(Setting(key=body.key, value=body.value, value_type=body.value_type))
    db.commit()
    return {"ok": True}


# ---------- Outbox (админ) ----------

@router.get("/events/outbox")
def list_outbox(admin: AdminUser, event_name: str = "", limit: int = 50,
                db: Session = Depends(get_db)):
    """Последние события шины из outbox (для отладки и smoke-проверок)."""
    query = select(EventOutbox).order_by(EventOutbox.id.desc()).limit(min(max(limit, 1), 500))
    if event_name:
        query = query.where(EventOutbox.event_name == event_name)
    return [
        {
            "id": row.id,
            "event_name": row.event_name,
            "payload": row.payload,
            "processed": row.processed,
            "created_at": row.created_at.isoformat(),
        }
        for row in db.scalars(query).all()
    ]


@router.get("/events/log")
def events_log(admin: AdminUser, action: str = "", limit: int = 50,
               db: Session = Depends(get_db)):
    """Журнал аудита events_log: кто, что, когда (первый шаг к экрану «Журналы»)."""
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 500))
    if action:
        query = query.where(AuditEvent.action == action)
    return [
        {
            "id": row.id,
            "user_id": str(row.user_id) if row.user_id else None,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in db.scalars(query).all()
    ]


# ---------- Система: бэкапы и версия (updates-and-backups-spec) ----------

class BackupOut(BaseModel):
    id: uuid.UUID
    file_name: str
    size: int
    sha256: str
    kind: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/system/backups", response_model=list[BackupOut])
def list_backups(admin: AdminUser, db: Session = Depends(get_db)):
    return db.scalars(select(Backup).order_by(Backup.created_at.desc())).all()


@router.post("/system/backups", status_code=202)
def start_backup(admin: AdminUser, db: Session = Depends(get_db)):
    """Ручной запуск бэкапа в фоне (Celery); статус — поллингом списка."""
    from src.core.tasks import backup_task

    backup_task.delay("manual")
    return {"ok": True, "queued": True}


@router.post("/system/backups/{backup_id}/verify", status_code=202)
def start_backup_verify(backup_id: uuid.UUID, admin: AdminUser,
                        db: Session = Depends(get_db)):
    from src.core.tasks import verify_backup_task

    if db.get(Backup, backup_id) is None:
        raise HTTPException(404, "Backup not found")
    verify_backup_task.delay(str(backup_id))
    return {"ok": True}


@router.get("/system/version")
def system_version(user: CurrentUser):
    """Текущая версия (src/__init__.__version__), канал и доступное обновление."""
    from src import __version__

    latest = db_latest_update_info()
    return {
        "version": __version__,
        "channel": get_settings().update_channel,
        "latest": latest,
    }


@router.post("/system/update/check")
def run_update_check(admin: AdminUser):
    """«Проверить сейчас» (admin): манифест по URL, подпись, сравнение версий."""
    from src.core.update.check import UpdateCheckError, check_update

    try:
        return check_update()
    except UpdateCheckError as exc:
        raise HTTPException(502, str(exc)) from exc


def db_latest_update_info() -> dict | None:
    """Информация о последней найденной проверке обновлений (этап C пишет в settings)."""
    from src.core.models import Setting

    db = SessionLocal()
    try:
        row = db.scalar(select(Setting).where(Setting.key == "update.available"))
        if row is None:
            return None
        value = row.value
        return value if isinstance(value, dict) else None
    finally:
        db.close()
