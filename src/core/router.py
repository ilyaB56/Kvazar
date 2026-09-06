"""API ядра: auth, пользователи, компании, контакты, настройки, события."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core import events
from src.core.auth import (
    MODULES,
    AdminUser,
    CurrentUser,
    HumanUser,
    WriteUser,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from src.core.models import (
    ApiToken,
    AuditEvent,
    Backup,
    Company,
    Contact,
    EventOutbox,
    RevokedToken,
    Role,
    RolePermission,
    Setting,
    User,
)
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
def change_password(body: ChangePasswordIn, user: HumanUser, db: Session = Depends(get_db)):
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
def me(user: HumanUser):
    return user


# ---------- Users (admin) ----------

@router.get("/users", response_model=list[UserOut])
def list_users(admin: AdminUser, db: Session = Depends(get_db)):
    return db.scalars(select(User)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: AdminUser, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email already exists")
    if db.get(Role, body.role) is None:
        raise HTTPException(422, f"Unknown role: {body.role}")
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


class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: uuid.UUID, body: UserPatch, admin: AdminUser,
               db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if body.role is not None:
        if db.get(Role, body.role) is None:
            raise HTTPException(422, f"Unknown role: {body.role}")
        if user.id == admin.id and body.role != "admin":
            raise HTTPException(400, "Нельзя снять роль администратора с себя")
        user.role = body.role
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.is_active is not None:
        user.is_active = body.is_active
        if not user.is_active:
            user.token_version += 1  # деактивация убивает выданные токены
    db.add(AuditEvent(action="user.updated", entity_type="user", entity_id=str(user.id),
                      payload=body.model_dump(exclude_none=True)))
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


# ---------- Роли и права (редизайн §6.2) ----------

class PermissionsOut(BaseModel):
    role: dict
    permissions: dict[str, str]

    model_config = {
        "json_schema_extra": {
            "example": {
                "role": {"key": "user", "name": "Пользователь"},
                "permissions": {
                    "accounting": "rw", "crm": "rw", "integrations": "rw",
                    "ai": "rw", "system": "ro",
                },
            }
        }
    }


class RoleOut(BaseModel):
    key: str
    name: str
    description: str
    is_builtin: bool
    color: str
    users_count: int
    permissions: dict[str, str]

    model_config = {
        "json_schema_extra": {
            "example": {
                "key": "user",
                "name": "Пользователь",
                "description": "Работа во всех разделах, настройка системы — только чтение",
                "is_builtin": True,
                "color": "teal",
                "users_count": 3,
                "permissions": {
                    "accounting": "rw", "crm": "rw", "integrations": "rw",
                    "ai": "rw", "system": "ro",
                },
            }
        }
    }


class RoleCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)
    color: str = Field(default="zinc", max_length=20)


class RolePermissionsIn(BaseModel):
    permissions: dict[str, str]

    model_config = {
        "json_schema_extra": {
            "example": {"permissions": {"accounting": "rw", "crm": "ro", "integrations": "none", "ai": "rw", "system": "none"}}
        }
    }


# Транслитерация для slug-ключа кастомной роли из названия
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def _role_slug(name: str) -> str:
    slug = name.lower().translate(_TRANSLIT)
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "role"


def _role_permissions(db: Session, role_key: str) -> dict[str, str]:
    if role_key == "admin":
        return dict.fromkeys(MODULES, "rw")
    rows = db.scalars(select(RolePermission).where(RolePermission.role_key == role_key)).all()
    levels = {row.module: row.level for row in rows}
    return {module: levels.get(module, "none") for module in MODULES}


@router.get("/me/permissions", response_model=PermissionsOut)
def my_permissions(user: CurrentUser, db: Session = Depends(get_db)):
    role = db.get(Role, user.role)
    return PermissionsOut(
        role={"key": user.role, "name": role.name if role else user.role},
        permissions=_role_permissions(db, user.role),
    )


@router.get("/roles", response_model=list[RoleOut])
def list_roles(admin: AdminUser, db: Session = Depends(get_db)):
    roles = db.scalars(select(Role).order_by(Role.is_builtin.desc(), Role.key)).all()
    counts: dict[str, int] = dict(
        db.execute(select(User.role, func.count()).group_by(User.role)).all()
    )
    return [
        RoleOut(
            key=role.key, name=role.name, description=role.description,
            is_builtin=role.is_builtin, color=role.color,
            users_count=counts.get(role.key, 0),
            permissions=_role_permissions(db, role.key),
        )
        for role in roles
    ]


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(body: RoleCreateIn, admin: AdminUser, db: Session = Depends(get_db)):
    key = _role_slug(body.name)
    if db.get(Role, key):
        raise HTTPException(409, f"Role key '{key}' already exists")
    role = Role(key=key, name=body.name, description=body.description,
                is_builtin=False, color=body.color)
    db.add(role)
    # новая роль стартует закрытой во всех модулях — админ откроет нужное
    for module in MODULES:
        db.add(RolePermission(role_key=key, module=module, level="none"))
    db.add(AuditEvent(user_id=admin.id, action="role.created",
                      entity_type="role", entity_id=key))
    db.commit()
    return RoleOut(key=key, name=role.name, description=role.description,
                   is_builtin=False, color=role.color, users_count=0,
                   permissions=_role_permissions(db, key))


@router.put("/roles/{key}/permissions", response_model=RoleOut)
def put_role_permissions(key: str, body: RolePermissionsIn,
                         admin: AdminUser, db: Session = Depends(get_db)):
    role = db.get(Role, key)
    if role is None:
        raise HTTPException(404, "Role not found")
    if role.key == "admin":
        raise HTTPException(400, "Роль администратора неизменяема")
    for module, level in body.permissions.items():
        if module not in MODULES:
            raise HTTPException(422, f"Unknown module: {module}")
        if level not in ("rw", "ro", "none"):
            raise HTTPException(422, f"Invalid level: {level}")
    for module in MODULES:
        level = body.permissions.get(module, "none")
        row = db.scalar(select(RolePermission).where(
            RolePermission.role_key == key, RolePermission.module == module))
        if level == "none":
            if row:
                db.delete(row)
            continue
        if row:
            row.level = level
        else:
            db.add(RolePermission(role_key=key, module=module, level=level))
    db.add(AuditEvent(user_id=admin.id, action="role.permissions.updated",
                      entity_type="role", entity_id=key,
                      payload={"permissions": body.permissions}))
    db.commit()
    counts: dict[str, int] = dict(
        db.execute(select(User.role, func.count()).group_by(User.role)).all()
    )
    return RoleOut(key=role.key, name=role.name, description=role.description,
                   is_builtin=role.is_builtin, color=role.color,
                   users_count=counts.get(role.key, 0),
                   permissions=_role_permissions(db, key))


@router.delete("/roles/{key}", response_model=RoleOut)
def delete_role(key: str, admin: AdminUser, db: Session = Depends(get_db)):
    role = db.get(Role, key)
    if role is None:
        raise HTTPException(404, "Role not found")
    if role.is_builtin:
        raise HTTPException(400, "Builtin roles cannot be deleted")
    users_count = db.scalar(select(func.count()).where(User.role == key))
    if users_count:
        raise HTTPException(409, f"Role has {users_count} users, reassign them first")
    out = RoleOut(key=role.key, name=role.name, description=role.description,
                  is_builtin=role.is_builtin, color=role.color, users_count=0,
                  permissions=_role_permissions(db, key))
    db.delete(role)  # каскад удаляет строки прав
    db.add(AuditEvent(user_id=admin.id, action="role.deleted",
                      entity_type="role", entity_id=key))
    db.commit()
    return out


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
        "ai_provider": get_settings().ai_provider,
    }


@router.post("/system/update/check")
def run_update_check(admin: AdminUser):
    """«Проверить сейчас» (admin): манифест по URL, подпись, сравнение версий."""
    from src.core.update.check import UpdateCheckError, check_update

    try:
        return check_update()
    except UpdateCheckError as exc:
        raise HTTPException(502, str(exc)) from exc


# ---------- Служебные API-токены (showcase-chain, этап A) ----------

class ApiTokenIn(BaseModel):
    name: str
    role: str = "user"


class ApiTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("/admin/api-tokens", response_model=list[ApiTokenOut])
def list_api_tokens(admin: AdminUser, db: Session = Depends(get_db)):
    return db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc())).all()


@router.post("/admin/api-tokens", status_code=201)
def create_api_token(body: ApiTokenIn, admin: AdminUser, db: Session = Depends(get_db)):
    """Токен показывается ровно один раз (как secret_token у webhook)."""
    import secrets

    token = secrets.token_urlsafe(32)
    row = ApiToken(
        name=body.name,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        role=body.role,
        owner_user_id=admin.id,
    )
    db.add(row)
    db.add(AuditEvent(action="api_token.created", entity_type="api_token",
                      payload={"name": body.name, "role": body.role}))
    db.commit()
    db.refresh(row)
    return {
        "id": row.id, "name": row.name, "role": row.role,
        "is_active": row.is_active, "created_at": row.created_at,
        "last_used_at": None, "token": token,
    }


@router.delete("/admin/api-tokens/{token_id}", response_model=ApiTokenOut)
def revoke_api_token(token_id: uuid.UUID, admin: AdminUser, db: Session = Depends(get_db)):
    """Удаление = отзыв: is_active=false + аудит api_token.revoked."""
    row = db.get(ApiToken, token_id)
    if row is None:
        raise HTTPException(404, "API token not found")
    row.is_active = False
    db.add(AuditEvent(action="api_token.revoked", entity_type="api_token",
                      entity_id=str(row.id), payload={"name": row.name}))
    db.commit()
    db.refresh(row)
    return row


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
