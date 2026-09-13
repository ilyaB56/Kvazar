"""API ядра: auth, пользователи, компании, контакты, настройки, события."""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta

from typing import Annotated

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
    PlatformAdmin,
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
    AuthSession,
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
from src.core.rate_limit import (
    check_login_rate_limit, check_password_confirm_rate_limit,
    reset_login_rate_limit, reset_password_confirm_rate_limit,
)
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
    # sessions-security §2.2: активные сеансы ПОСЛЕ этого входа (включая
    # текущий); >1 — UI показывает модалку «в аккаунт уже вошли»
    active_sessions: int = 1
    # multitenancy §7.1: 2FA включена → вместо пары mfa_token (этап C);
    # этап A — всегда false. pl-админу здесь же реестр организаций.
    has_2fa: bool = False
    organizations: list[dict] = []


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutOthersIn(RefreshIn):
    """sessions-security §2.2 (дополнение 2026-09-12): разрушительное
    действие над сеансами — с подтверждением паролем."""
    password: str


class SessionRevokeIn(BaseModel):
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool = False
    # multitenancy: контекст (заполняется в /auth/me; в списках опускается)
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    is_platform_admin: bool = False

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
    # multitenancy §6: вход в деактивированную организацию запрещён
    # (вебхуки её коннекторов продолжают копиться — О4)
    if user.company_id:
        company = db.get(Company, user.company_id)
        if company is None or not company.is_active:
            raise HTTPException(403, "organization_disabled")
    # sessions-security §2.1: сеанс = sid пары токенов; user_agent/ip —
    # данные для показа самому пользователю (в события шины не идут)
    from src.config import get_settings as _gs
    sid = uuid.uuid4()
    # IP = ПОСЛЕДНИЙ адрес X-Forwarded-For (sessions-security §2.4,
    # ревью 2026-09-11): его дописывает наш nginx, равен реальному
    # remote_addr; первый адрес клиент подделывает инъекцией заголовка
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
    db.add(AuthSession(
        id=sid, user_id=user.id,
        user_agent=(request.headers.get("user-agent") or "")[:256],
        ip=(forwarded or (request.client.host if request.client else ""))[:64],
    ))
    db.add(
        AuditEvent(user_id=user.id, action="login", entity_type="user", entity_id=str(user.id))
    )
    db.flush()
    ttl_cut = datetime.now(UTC) - timedelta(days=_gs().refresh_expire_days)
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.created_at >= ttl_cut,
    )) or 1
    db.commit()
    # multitenancy §7.1: супер-админу — токены без org + реестр; обычному —
    # org его компании. (2FA-ветка — этап C: сейчас has_2fa всегда false)
    pl = bool(user.is_platform_admin)
    org = None if pl else (str(user.company_id) if user.company_id else None)
    orgs = [{"id": str(c.id), "name": c.name} for c in db.scalars(
        select(Company).where(Company.is_active.is_(True))
        .order_by(Company.created_at)).all()] if pl else []
    return TokenOut(
        access_token=create_access_token(user.id, user.role,
                                         ver=user.token_version, sid=sid,
                                         org=org, pl=pl),
        refresh_token=create_refresh_token(user.id, ver=user.token_version,
                                           sid=sid, org=org, pl=pl),
        active_sessions=int(active),
        has_2fa=False,
        organizations=orgs,
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
    # sessions-security §2.1: сеанс отозван (logout-others/revoke) —
    # refresh запрещён; живой — продлевается (rotация сохраняет sid)
    sid = payload.get("sid")
    session = None
    if sid:
        session = db.get(AuthSession, uuid.UUID(sid))
        if session is not None:
            if session.revoked_at is not None or session.user_id != user.id:
                raise HTTPException(401, "Session revoked")
            session.last_used_at = datetime.now(UTC)
    # ротация в том же тенант-контексте (multitenancy §2 п.2-3)
    org = payload.get("org")
    pl = bool(payload.get("pl"))
    return TokenOut(
        access_token=create_access_token(user.id, user.role,
                                         ver=user.token_version, sid=sid,
                                         org=org, pl=pl),
        refresh_token=create_refresh_token(user.id, ver=user.token_version,
                                           sid=sid, org=org, pl=pl),
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
    sid = payload.get("sid")
    if sid:
        session = db.get(AuthSession, uuid.UUID(sid))
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
    db.add(AuditEvent(user_id=user.id, action="logout", entity_type="user", entity_id=str(user.id)))
    db.commit()
    return {"ok": True}


# ---------- Сеансы входа (sessions-security-spec §2.2) ----------

def _session_cutoff() -> datetime:
    from src.config import get_settings as _gs
    return datetime.now(UTC) - timedelta(days=_gs().refresh_expire_days)


def _confirm_password(user, password: str) -> None:
    """Подтверждение паролем (§2.2): неверный → 403 wrong_password (не
    раскрывает валидность); брутфорс — Redis-счётчик 5/60с по user_id
    (429). Успех сбрасывает счётчик. Вызывается ПОСЛЕ auth-проверок —
    мусорные токены счётчик не жгут."""
    check_password_confirm_rate_limit(user.id)
    if not password or not verify_password(password, user.password_hash):
        raise HTTPException(403, "wrong_password")
    reset_password_confirm_rate_limit(user.id)


@router.post("/auth/logout-others")
def logout_others(body: LogoutOthersIn, db: Session = Depends(get_db)):
    """Завершить все прочие активные сеансы; текущий (по refresh-токену)
    остаётся. Логин не блокируется — это выбор пользователя (§2.2);
    завершение — с подтверждением паролем (дополнение основателя)."""
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
    sid = payload.get("sid")
    if not sid:
        raise HTTPException(401, "Token has no session")
    current = db.get(AuthSession, uuid.UUID(sid))
    if current is None or current.revoked_at is not None or current.user_id != user.id:
        raise HTTPException(401, "Session revoked")
    _confirm_password(user, body.password)
    now = datetime.now(UTC)
    others = db.scalars(select(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.created_at >= _session_cutoff(),
        AuthSession.id != current.id,
    )).all()
    for session in others:
        session.revoked_at = now
    db.add(AuditEvent(
        user_id=user.id, action="auth.sessions.logout_others",
        entity_type="user", entity_id=str(user.id),
        payload={"terminated": len(others)},
    ))
    db.commit()
    return {"terminated": len(others)}


class SelectOrgIn(BaseModel):
    """multitenancy §7.1: выбор организации супер-админом — по refresh-токену
    (как logout-others); новая пара с org, sid сохраняется, прежняя пара
    остаётся валидной в другой вкладке (один сеанс — много контекстов)."""
    refresh_token: str
    company_id: uuid.UUID


class LeaveOrgIn(BaseModel):
    refresh_token: str


def _reissue_pair(db, payload, user, org: str | None) -> TokenOut:
    """Перевыпуск пары в другом тенант-контексте с сохранением sid."""
    sid = payload.get("sid")
    pl = True  # контекст меняет только платформенный админ
    db.add(AuditEvent(user_id=user.id,
                      action="auth.org.selected" if org else "auth.org.left",
                      entity_type="company",
                      entity_id=org or "",
                      payload={"sid": sid}))
    db.commit()
    return TokenOut(
        access_token=create_access_token(user.id, user.role,
                                         ver=user.token_version, sid=sid,
                                         org=org, pl=pl),
        refresh_token=create_refresh_token(user.id, ver=user.token_version,
                                           sid=sid, org=org, pl=pl),
    )


def _validate_refresh_for_context(body, db) -> tuple:
    """Общие проверки refresh-токена для смены контекста (как logout-others)."""
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
    sid = payload.get("sid")
    if not sid:
        raise HTTPException(401, "Token has no session")
    session = db.get(AuthSession, uuid.UUID(sid))
    if session is None or session.revoked_at is not None or session.user_id != user.id:
        raise HTTPException(401, "Session revoked")
    return payload, user


@router.post("/auth/select-org", response_model=TokenOut)
def select_org(body: SelectOrgIn, db: Session = Depends(get_db)):
    """Выбор организации платформенным админом (multitenancy §7.1): только
    is_platform_admin, по refresh-токену; новая пара с org=выбранная."""
    payload, user = _validate_refresh_for_context(body, db)
    if not user.is_platform_admin:
        raise HTTPException(403, "Platform admin only")
    company = db.get(Company, body.company_id)
    if company is None:
        raise HTTPException(404, "Organization not found")
    if not company.is_active:
        raise HTTPException(403, "organization_disabled")
    return _reissue_pair(db, payload, user, str(company.id))


@router.post("/auth/leave-org", response_model=TokenOut)
def leave_org(body: LeaveOrgIn, db: Session = Depends(get_db)):
    """Возврат в платформенный контекст (без org), sid сохраняется."""
    payload, user = _validate_refresh_for_context(body, db)
    if not user.is_platform_admin:
        raise HTTPException(403, "Platform admin only")
    return _reissue_pair(db, payload, user, None)


class AuthSessionOut(BaseModel):
    id: uuid.UUID
    user_agent: str
    ip: str
    created_at: datetime
    last_used_at: datetime | None
    is_current: bool = False


@router.get("/auth/sessions", response_model=list[AuthSessionOut])
def list_auth_sessions(request: Request, user: HumanUser, db: Session = Depends(get_db)):
    """Свои активные сеансы; текущий помечен is_current (по sid access-токена).
    Заодно — уборка: протухшие по TTL помечаются revoked_at (§2.1)."""
    from jwt import PyJWTError

    now = datetime.now(UTC)
    db.execute(
        AuthSession.__table__.update().where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.created_at < _session_cutoff(),
        ).values(revoked_at=now)
    )
    current_sid = None
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            current_sid = decode_token(auth_header[7:]).get("sid")
        except PyJWTError:
            current_sid = None
    rows = db.scalars(select(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.created_at >= _session_cutoff(),
    ).order_by(AuthSession.created_at.desc())).all()
    db.commit()
    return [AuthSessionOut(
        id=row.id, user_agent=row.user_agent, ip=row.ip,
        created_at=row.created_at, last_used_at=row.last_used_at,
        is_current=current_sid is not None and str(row.id) == current_sid,
    ) for row in rows]


@router.post("/auth/sessions/{session_id}/revoke")
def revoke_auth_session(session_id: uuid.UUID, body: SessionRevokeIn, request: Request,
                        user: HumanUser, db: Session = Depends(get_db)):
    """Завершить свой сеанс (кроме текущего — для него есть logout) с
    подтверждением паролем (§2.2). Чужой/несуществующий — 404 (не
    раскрываем чужие id — в том числе при неверном пароле)."""
    session = db.get(AuthSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(404, "Session not found")
    _confirm_password(user, body.password)
    if session.revoked_at is not None:
        raise HTTPException(409, "Session already revoked")
    from jwt import PyJWTError

    current_sid = None
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            current_sid = decode_token(auth_header[7:]).get("sid")
        except PyJWTError:
            current_sid = None
    if current_sid and str(session.id) == current_sid:
        raise HTTPException(409, "Current session: use logout instead")
    session.revoked_at = datetime.now(UTC)
    db.add(AuditEvent(
        user_id=user.id, action="auth.session.revoked",
        entity_type="auth_session", entity_id=str(session.id),
    ))
    db.commit()
    return {"ok": True, "revoked": str(session.id)}


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
    # sessions-security §2.2: смена пароля завершает ВСЕ сеансы каскадом
    now = datetime.now(UTC)
    for session in db.scalars(select(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))).all():
        session.revoked_at = now
    db.add(AuditEvent(
        user_id=user.id, action="password.changed", entity_type="user", entity_id=str(user.id)
    ))
    db.commit()
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: HumanUser, db: Annotated[Session, Depends(get_db)] = None):
    # гейт 1.4: seed-админ, ни разу не менявший пароль (нет password.changed
    # в журнале), получает must_change_password — UI показывает предупреждение
    changed = db.scalar(select(AuditEvent.id).where(
        AuditEvent.entity_type == "user", AuditEvent.entity_id == str(user.id),
        AuditEvent.action == "password.changed",
    ).limit(1))
    # временный пароль от платформы и без смены — тоже «смените пароль»
    temp_issued = changed is None and db.scalar(select(AuditEvent.id).where(
        AuditEvent.entity_type == "user", AuditEvent.entity_id == str(user.id),
        AuditEvent.action == "platform.user.temp_password",
    ).limit(1))
    payload = UserOut.model_validate(user)
    payload.must_change_password = bool(
        (changed is None and user.email == "admin@example.com") or temp_issued)
    # тенант-контекст — из клейма org (для бейджа организации в шапке)
    org = getattr(user, "token_org", None)
    company = db.get(Company, uuid.UUID(str(org))) if org else None
    payload.company_id = company.id if company else None
    payload.company_name = company.name if company else None
    payload.is_platform_admin = bool(user.is_platform_admin)
    return payload


# ---------- Users (admin) ----------

@router.get("/users", response_model=list[UserOut])
def list_users(admin: AdminUser, db: Session = Depends(get_db)):
    # multitenancy §9: срез своей организации; платформенный админ в контексте
    # org видит её, без org — всех (реестр платформы)
    org = getattr(admin, "token_org", None)
    if getattr(admin, "token_pl", False):
        if not org:
            return db.scalars(select(User)).all()
        return db.scalars(select(User).where(User.company_id == uuid.UUID(str(org)))).all()
    return db.scalars(select(User).where(
        User.company_id == admin.company_id)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: AdminUser, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email already exists")
    if db.get(Role, body.role) is None:
        raise HTTPException(422, f"Unknown role: {body.role}")
    # multitenancy §9: компания наследуется от создателя, не из тела.
    # Платформенный контекст без org компании не имеет — создавать
    # пользователей нужно внутри организации (select-org).
    creator_org = getattr(admin, "token_org", None) or (
        None if admin.is_platform_admin else admin.company_id)
    if creator_org is None:
        raise HTTPException(403, "no_company_context: select an organization first")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        company_id=creator_org,
    )
    db.add(user)
    db.add(AuditEvent(user_id=admin.id, action="user.created", entity_type="user",
                      entity_id=str(user.id), payload={"email": body.email}))
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


# ---------- Платформа: организации (multitenancy §7.3, только pl) ----------

def _temp_password() -> str:
    """Временный пароль по политике (цифры+буквы обоих регистров, 12)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "Kv-" + "".join(secrets.choice(alphabet) for _ in range(9))
        if not validate_password(pwd):
            return pwd


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    inn: str
    is_active: bool
    users_count: int = 0
    created_at: object = None

    model_config = {"from_attributes": True}


class OrgCreateIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "name": "ООО «Ромашка»", "inn": "7707083893",
        "admin_email": "director@romashka.ru", "admin_full_name": "Иван Иванов",
    }}}

    name: str = Field(min_length=2, max_length=255)
    inn: str = Field(default="", max_length=12)
    admin_email: str = Field(max_length=255)
    admin_full_name: str = Field(default="", max_length=255)


class OrgPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    inn: str | None = Field(default=None, max_length=12)
    is_active: bool | None = None


@router.get("/platform/orgs", response_model=list[OrgOut])
def platform_list_orgs(admin: PlatformAdmin, db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(User.company_id, func.count(User.id))
        .where(User.company_id.is_not(None))
        .group_by(User.company_id)).all())
    return [OrgOut(id=c.id, name=c.name, inn=c.inn, is_active=c.is_active,
                   users_count=int(counts.get(c.id, 0)), created_at=c.created_at)
            for c in db.scalars(select(Company).order_by(Company.created_at)).all()]


@router.post("/platform/orgs", status_code=201)
def platform_create_org(body: OrgCreateIn, admin: PlatformAdmin,
                        db: Session = Depends(get_db)):
    """Организация + её админ (роль admin) + временный пароль — показ один
    раз. Сид стартовых данных организации (категории/стадии/склады) — этап B,
    когда у модульных таблиц появится company_id."""
    if db.scalar(select(User).where(User.email == body.admin_email)):
        raise HTTPException(409, f"Email already exists: {body.admin_email}")
    company = Company(name=body.name, inn=body.inn, is_active=True)
    db.add(company)
    db.flush()
    # стартовые данные организации: категории, склады+транзиты, период (§5.1)
    from src.seed import seed_company_data

    seed_company_data(db, company.id)
    temp_password = _temp_password()
    org_admin = User(
        email=body.admin_email,
        password_hash=hash_password(temp_password),
        full_name=body.admin_full_name,
        role="admin",
        company_id=company.id,
    )
    db.add(org_admin)
    db.flush()
    db.add(AuditEvent(
        user_id=admin.id, action="platform.org.created",
        entity_type="company", entity_id=str(company.id),
        payload={"name": company.name, "admin_email": org_admin.email}))
    db.add(AuditEvent(
        user_id=admin.id, action="platform.user.temp_password",
        entity_type="user", entity_id=str(org_admin.id),
        payload={"org_id": str(company.id)}))
    events.publish(db, "platform.org.created", {
        "org_id": str(company.id), "name": company.name,
        "admin_email": org_admin.email,
    })
    db.commit()
    db.refresh(company)
    db.refresh(org_admin)
    return {
        "id": str(company.id), "name": company.name, "inn": company.inn,
        "is_active": company.is_active, "users_count": 1,
        "created_at": company.created_at,
        "admin": {"id": str(org_admin.id), "email": org_admin.email,
                  "full_name": org_admin.full_name, "role": "admin",
                  "must_change_password": True},
        "temp_password": temp_password,
    }


@router.patch("/platform/orgs/{company_id}", response_model=OrgOut)
def platform_patch_org(company_id: uuid.UUID, body: OrgPatchIn,
                       admin: PlatformAdmin, db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "Organization not found")
    was_active = company.is_active
    if body.name is not None:
        company.name = body.name
    if body.inn is not None:
        company.inn = body.inn
    if body.is_active is not None:
        company.is_active = body.is_active
    db.add(AuditEvent(
        user_id=admin.id,
        action="platform.org.deactivated" if was_active and not company.is_active
        else "platform.org.updated",
        entity_type="company", entity_id=str(company.id),
        payload=body.model_dump(exclude_none=True)))
    events.publish(db, "platform.org.deactivated" if was_active and not company.is_active
                   else "platform.org.updated",
                   {"org_id": str(company.id), "name": company.name})
    db.commit()
    db.refresh(company)
    users_count = db.scalar(select(func.count()).select_from(User).where(
        User.company_id == company.id)) or 0
    return OrgOut(id=company.id, name=company.name, inn=company.inn,
                  is_active=company.is_active, users_count=int(users_count),
                  created_at=company.created_at)


@router.post("/platform/users/{user_id}/reset-password")
def platform_reset_password(user_id: uuid.UUID, admin: PlatformAdmin,
                            db: Session = Depends(get_db)):
    """Сброс пароля любого пользователя платформой: временный пароль один
    раз, token_version+=1 (старые токены и сеансы умирают), аудит."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    temp_password = _temp_password()
    target.password_hash = hash_password(temp_password)
    target.token_version += 1
    now = datetime.now(UTC)
    for session in db.scalars(select(AuthSession).where(
            AuthSession.user_id == target.id,
            AuthSession.revoked_at.is_(None))).all():
        session.revoked_at = now
    db.add(AuditEvent(
        user_id=admin.id, action="platform.password.reset",
        entity_type="user", entity_id=str(target.id),
        payload={"email": target.email}))
    db.add(AuditEvent(
        user_id=admin.id, action="platform.user.temp_password",
        entity_type="user", entity_id=str(target.id), payload={}))
    db.commit()
    return {"temp_password": temp_password, "must_change_password": True}


# ---------- Companies / Contacts ----------

@router.get("/companies", response_model=list[CompanyIn])
def list_companies(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Company)).all()


class CompanyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    inn: str | None = Field(default=None, max_length=12)

    model_config = {
        "json_schema_extra": {"example": {"name": "ООО «ТехноПром»", "inn": "7707083893"}}
    }


@router.patch("/companies/{company_id}", response_model=CompanyIn)
def patch_company(company_id: uuid.UUID, body: CompanyPatch, admin: AdminUser,
                  db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    if body.name is not None:
        company.name = body.name
    if body.inn is not None:
        company.inn = body.inn
    db.add(AuditEvent(user_id=admin.id, action="company.updated",
                      entity_type="company", entity_id=str(company_id),
                      payload=body.model_dump(exclude_none=True)))
    db.commit()
    db.refresh(company)
    return company


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
        # контекст организации создателя (для pl — выбранная org; для юзера
        # организации — его компания); машинные вызовы пишут в её данные
        company_id=getattr(admin, "token_org", None) or admin.company_id,
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
