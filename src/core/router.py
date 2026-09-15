"""API ядра: auth, пользователи, компании, контакты, настройки, события."""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from datetime import UTC, date, datetime, timedelta

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
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
    current_company,
    get_current_user,
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from src.core.models import (
    ApiToken,
    PasswordReset,
    UserPermission,
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
from src.core.versioning import record_version
from src.core.rate_limit import (
    check_forgot_password_rate_limit, check_login_rate_limit,
    check_password_confirm_rate_limit, reset_login_rate_limit,
    reset_password_confirm_rate_limit,
)
from src.db import SessionLocal, get_db

router = APIRouter(tags=["core"])


# ---------- Schemas ----------

class LoginIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "email": "user@company.ru  # или username, например IIIVANOV",
        "password": "…"}}}

    email: str  # вход по username ИЛИ email (role-delegation §12)
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # sessions-security §2.2: активные сеансы ПОСЛЕ этого входа (включая
    # текущий); >1 — UI показывает модалку «в аккаунт уже вошли»
    active_sessions: int = 1
    # multitenancy §7.1: 2FA включена → вместо пары mfa_token (5 минут,
    # не сеанс). Настройка — мастер UI по флагу totp_setup_required.
    has_2fa: bool = False
    mfa_required: bool = False
    mfa_token: str = ""
    # руководитель (admin/pl) без включённой 2FA — UI запускает мастер;
    # mfa_setup_required = дедлайн 7 дней прошёл: вход блокируется
    # до завершения настройки (этап D)
    totp_setup_required: bool = False
    mfa_setup_required: bool = False
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
    # datetime сериализуется в ISO-строку ответом (аннотация datetime —
    # из ORM приходит datetime, str ломал model_validate)
    must_change_password_by: datetime | None = None
    username: str | None = None
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
    login = body.email.strip()
    user = db.scalar(select(User).where(
        or_(User.email == login, User.username == login)))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Bad credentials")
    reset_login_rate_limit(request)
    # multitenancy §6: вход в деактивированную организацию запрещён
    # (вебхуки её коннекторов продолжают копиться — О4)
    if user.company_id:
        company = db.get(Company, user.company_id)
        if company is None or not company.is_active:
            raise HTTPException(403, "organization_disabled")
    # 2FA (multitenancy §7.1, О1: обязательна для admin И is_platform_admin):
    # включена → mfa_token вместо пары; обязательна, но не настроена →
    # mfa_token; настройка — мастер UI (totp_setup_required)
    from src.core import totp as totp_core
    if totp_core.is_enabled(db, user.id):
        db.commit()
        return TokenOut(
            access_token="", refresh_token="",
            mfa_required=True, mfa_token=create_mfa_token(user.id))
    setup_recommended = _2fa_required(user) and not totp_core.is_enabled(db, user.id)
    # дедлайн 2FA (этап D, ревью C): руководитель без 2FA дольше 7 дней —
    # вход блокируется до настройки (мастер работает по mfa_token)
    deadline = (user.totp_setup_deadline.date()
                if user.totp_setup_deadline else None)
    if setup_recommended and deadline is not None and date.today() > deadline:
        db.commit()
        return TokenOut(
            access_token="", refresh_token="",
            mfa_setup_required=True, mfa_token=create_mfa_token(user.id))

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
        has_2fa=totp_core.is_enabled(db, user.id),
        # UI: руководитель без 2FA — мастер настройки при первом входе
        # (задание этапа C: пару не блокируем, внедрение через мастер)
        totp_setup_required=setup_recommended,
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


# ---------- Жизненный цикл учётки (role-delegation §12) ----------

# транслитерация ФИО → логин «инициалы+фамилия» (§12.2)
_USERNAME_TRANSLIT = {
    "а": "A", "б": "B", "в": "V", "г": "G", "д": "D", "е": "E", "ё": "E",
    "ж": "ZH", "з": "Z", "и": "I", "й": "I", "к": "K", "л": "L", "м": "M",
    "н": "N", "о": "O", "п": "P", "р": "R", "с": "S", "т": "T", "у": "U",
    "ф": "F", "х": "KH", "ц": "TS", "ч": "CH", "ш": "SH", "щ": "SHCH",
    "ъ": "", "ы": "Y", "ь": "", "э": "E", "ю": "IU", "я": "IA",
}


def _username_from_full_name(db: Session, full_name: str) -> str:
    """Иван Иванович Иванов → IIIVANOV; совпадение → IIIVANOV1, 2…
    (индекс — первый свободный). Верхний регистр, без разделителей."""
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        raise HTTPException(422, "full_name required for username generation")
    surname = _translit(parts[-1])
    initials = "".join(_translit(p)[0] for p in parts[:-1] if _translit(p))
    base = (initials + surname)[:64]
    if db.scalar(select(User.id).where(User.username == base)) is None:
        return base
    index = 1
    while db.scalar(select(User.id).where(
            User.username == f"{base}{index}")) is not None:
        index += 1
    return f"{base}{index}"


def _translit(text: str) -> str:
    return "".join(_USERNAME_TRANSLIT.get(ch, ch.upper() if ch.isalpha() else "")
                   for ch in text.lower())


class AccountCreateIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "full_name": "Иван Иванович Иванов",
        "email": "ivanov@company.ru", "phone": "+79001234567",
    }}}

    full_name: str = Field(min_length=3, max_length=255)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=64)


@router.post("/accounts", status_code=201)
def create_account_v2(body: AccountCreateIn,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Создать «пустую» учётку (§12.1): ФИО + email + телефон; логин
    генерируется из ФИО; без роли и прав (роль 'readonly' — экран
    «Нет доступа» при входе). Право: admin ИЛИ rw ≥1 модуль.
    company_id наследуется от создателя (контекст)."""
    if user.role != "admin" and not _can_delegate(user, db):
        raise HTTPException(403, "Requires rw on at least one module")
    creator_org = getattr(user, "token_org", None) or user.company_id
    if creator_org is None:
        raise HTTPException(403, "no_company_context: select an organization")
    if body.email and db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, f"Email already exists: {body.email}")
    username = _username_from_full_name(db, body.full_name)
    account = User(
        email=body.email or f"{username.lower()}@no-email.local",
        username=username,
        password_hash=hash_password(_temp_password()),  # вход до активации не нужен
        full_name=body.full_name,
        role="readonly",  # «пустая»: доступ только к экрану «Нет доступа»
        company_id=creator_org,
        is_active=True,
    )
    db.add(account)
    # телефон — в контакт (если email есть); v1: сохраняем в full_name только
    db.add(AuditEvent(
        user_id=user.id, action="account.created",
        entity_type="user", entity_id=str(account.id),
        payload={"username": username, "email": body.email[:120],
                 "created_via": "settings"}))
    db.commit()
    db.refresh(account)
    return {"id": str(account.id), "username": username,
            "email": account.email, "full_name": account.full_name,
            "role": account.role, "is_active": account.is_active}


# ---------- Делегирование прав (role-delegation §6) ----------

def _can_delegate(user, db: Session) -> bool:
    """admin или обладатель rw хотя бы на один модуль (GET /delegations)."""
    from src.core.auth import effective_module_level

    if user.role == "admin":
        return True
    return any(effective_module_level(db, user, m) == "rw" for m in MODULES)


def _same_org(db: Session, a, b) -> bool:
    """§16: выдающий и получатель — в одной организации. Организация
    выдающего — из его контекста (token_org — строка из JWT): у
    платформенного админа users.company_id = NULL, но в контексте org
    он действует в ней. Сравнение через UUID (строка ≠ UUID в Python)."""
    org = getattr(a, "token_org", None) or a.company_id
    if org is None or b.company_id is None:
        return False
    return uuid.UUID(str(org)) == b.company_id


def _cascade_revoke(db: Session, grantor_id: uuid.UUID, module: str,
                    reason: str):
    """Рекурсивный отзыв выдач, опирающихся на утраченное право (§3, Р2):
    фикс-поинт по granted_by; немедленный, в той же транзакции; каждая —
    аудит permission.revoked.cascade + record_versions."""
    from src.core.auth import effective_module_level

    grantor = db.get(User, grantor_id)
    if grantor is None or grantor.role == "admin":
        return
    if effective_module_level(db, grantor, module) == "rw":
        return
    rows = db.scalars(select(UserPermission).where(
        UserPermission.granted_by == grantor_id,
        UserPermission.module == module)).all()
    for row in rows:
        db.delete(row)
        db.add(AuditEvent(
            user_id=grantor_id, action="permission.revoked.cascade",
            entity_type="user", entity_id=str(row.user_id),
            payload={"module": module, "level": row.level, "reason": reason}))
        record_version(db, "user", str(row.user_id), grantor_id,
                       {f"permission.{module}": {"old": row.level, "new": "none"}},
                       reason=f"cascade: {reason}")
        _cascade_revoke(db, row.user_id, module, reason)


class DelegationsOut(BaseModel):
    grantable: dict[str, str]
    grants: list[dict]


@router.get("/delegations", response_model=DelegationsOut)
def my_delegations(user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """«Кого могу наделять»: свои rw-модули как доступные к выдаче + свои
    выдачи. Доступ: admin или rw хотя бы на один модуль (иначе 403)."""
    from src.core.auth import effective_module_level

    if not _can_delegate(user, db):
        raise HTTPException(403, "Requires rw on at least one module")
    if user.role == "admin":
        grantable = {m: "rw" for m in MODULES}
    else:
        grantable = {m: "rw" for m in MODULES
                     if effective_module_level(db, user, m) == "rw"}
    rows = db.scalars(select(UserPermission).where(
        UserPermission.granted_by == user.id)).all()
    users = {u.id: u for u in db.scalars(select(User).where(
        User.id.in_([r.user_id for r in rows]))).all()} if rows else {}
    grants = [{"user_id": str(r.user_id),
               "email": users[r.user_id].email if r.user_id in users else "",
               "full_name": (users[r.user_id].full_name
                             if r.user_id in users else ""),
               "module": r.module, "level": r.level,
               "granted_at": r.granted_at} for r in rows]
    return DelegationsOut(grantable=grantable, grants=grants)


class PermissionPutIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "module": "accounting", "level": "ro"}}}

    module: str
    level: str


@router.put("/users/{user_id}/permissions")
def put_permissions(user_id: uuid.UUID, body: PermissionPutIn,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Выдать/обновить личное право (§6 иерархия): grantee активен, ≠
    выдающего и в той же org (§16); module/level валидны; выдающий имеет
    rw на модуль (анти-эскалация 403); upsert last-writer-wins + аудит
    permission.granted + record_versions получателя."""
    from src.core.auth import effective_module_level

    grantee = db.get(User, user_id)
    if grantee is None or not _same_org(db, user, grantee):
        raise HTTPException(404, "User not found")
    if body.module not in MODULES or body.level not in ("rw", "ro"):
        raise HTTPException(422, "module must be one of MODULES; level rw|ro")
    if grantee.id == user.id:
        raise HTTPException(422, "cannot grant to yourself")
    if not grantee.is_active:
        raise HTTPException(422, "user is inactive")
    if user.role != "admin" and \
            effective_module_level(db, user, body.module) != "rw":
        raise HTTPException(403, "requires rw on the module to delegate")

    row = db.get(UserPermission, (grantee.id, body.module))
    old_level = row.level if row is not None else "none"
    if row is None:
        row = UserPermission(user_id=grantee.id, module=body.module)
        db.add(row)
    row.level = body.level
    row.granted_by = user.id

    # §12.3: первая выдача прав = активация учётки → временный пароль
    # (показ один раз; дедлайн смены +72ч) — только если пароль ещё не
    # активирован (нет прошлых выдач/смены пароля)
    temp_password = None
    first_activation = (old_level == "none"
                        and not db.scalars(select(UserPermission).where(
                            UserPermission.user_id == grantee.id)).all()
                        and grantee.must_change_password_by is None
                        and not db.scalar(select(AuditEvent.id).where(
                            AuditEvent.entity_type == "user",
                            AuditEvent.entity_id == str(grantee.id),
                            AuditEvent.action == "password.changed",
                        ).limit(1)))
    if first_activation:
        temp_password = _temp_password()
        grantee.password_hash = hash_password(temp_password)
        grantee.token_version += 1
        grantee.must_change_password_by = datetime.now(UTC) + timedelta(hours=72)
        db.add(AuditEvent(
            user_id=user.id, action="platform.user.temp_password",
            entity_type="user", entity_id=str(grantee.id),
            payload={"reason": "first grant activation"}))

    db.add(AuditEvent(
        user_id=user.id, action="permission.granted",
        entity_type="user", entity_id=str(grantee.id),
        payload={"module": body.module, "level": body.level,
                 "grantee": grantee.email}))
    record_version(db, "user", str(grantee.id), user.id,
                   {f"permission.{body.module}":
                    {"old": old_level, "new": body.level}},
                   reason="delegation")
    db.commit()
    result = {"user_id": str(grantee.id), "module": body.module,
              "level": body.level, "old_level": old_level}
    if temp_password is not None:
        result["temp_password"] = temp_password
        result["must_change_password_by"] =             grantee.must_change_password_by.isoformat()
    return result


@router.delete("/users/{user_id}/permissions/{module}")
def delete_permissions(user_id: uuid.UUID, module: str,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Отозвать личное право: только свои выдачи (admin — любые, чужие —
    403). Каскад: если после отзыва у получателя нет rw на модуль — его
    выдачи на модуль откатываются рекурсивно (§3)."""
    grantee = db.get(User, user_id)
    if grantee is None or not _same_org(db, user, grantee):
        raise HTTPException(404, "User not found")
    if module not in MODULES:
        raise HTTPException(422, f"Unknown module: {module}")
    row = db.get(UserPermission, (grantee.id, module))
    if row is None:
        raise HTTPException(404, "Permission not found")
    if user.role != "admin" and row.granted_by != user.id:
        raise HTTPException(403, "Can revoke only own grants")
    old_level = row.level
    db.delete(row)
    db.add(AuditEvent(
        user_id=user.id, action="permission.revoked",
        entity_type="user", entity_id=str(grantee.id),
        payload={"module": module, "level": old_level,
                 "grantee": grantee.email}))
    record_version(db, "user", str(grantee.id), user.id,
                   {f"permission.{module}": {"old": old_level, "new": "none"}},
                   reason="delegation revoke")
    db.flush()  # autoflush=False: без сброса каскад увидит удалённую строку
    _cascade_revoke(db, grantee.id, module, "revoke")
    db.commit()
    return {"user_id": str(grantee.id), "module": module, "revoked": old_level}


# ---------- Восстановление пароля (multitenancy §7.5, этап D) ----------

class ForgotPasswordIn(BaseModel):
    """Всегда 200 {ok}: письмо (если адрес существует) со ссылкой
    /reset-password?token=… (1 час, одноразовая); лимит 3/час."""
    model_config = {"json_schema_extra": {"example": {"login": "user@company.ru"}}}

    login: str = Field(max_length=255)


class ResetPasswordIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "token": "urlsafe-32-символа из письма",
        "new_password": "NewStrong1pass",
    }}}

    token: str = Field(min_length=20, max_length=64)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordIn, request: Request,
                    db: Session = Depends(get_db)):
    """Публичный. ВСЕГДА 200 {ok: true} — не раскрывает существование
    логина. Существующему активному пользователю: токен (urlsafe-32,
    sha256-хэш в БД, 1 час, одноразовый) + событие
    core.password.reset_requested (БЕЗ токена) — письмо шлёт обработчик
    integrations через платформенный SMTP. Rate limit 3/час (login, ip)."""
    import hashlib
    import secrets as _secrets

    ip = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()         or (request.client.host if request.client else "")
    check_forgot_password_rate_limit(f"{body.login.strip().lower()}|{ip}")

    user = db.scalar(select(User).where(User.email == body.login.strip()))
    if user is not None and user.is_active:
        token = _secrets.token_urlsafe(32)
        from src.core.crypto import encrypt_str

        db.add(PasswordReset(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            token_enc=encrypt_str(token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_ip=ip[:64],
        ))
        db.add(AuditEvent(user_id=user.id, action="password.reset_requested",
                          entity_type="user", entity_id=str(user.id)))
        events.publish(db, "core.password.reset_requested", {
            "user_id": str(user.id), "email": user.email,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            # токен в событии НЕТ: обработчик читает живую строку из БД
        })
    db.commit()
    return {"ok": True}


@router.post("/auth/reset-password")
def reset_password(body: ResetPasswordIn, db: Session = Depends(get_db)):
    """Публичный. Политика паролей; успех: hash, token_version+=1, ревок
    всех сеансов, used_at. Единый ответ за истёкшим/использованным/
    несуществующим — 410 reset_token_invalid (не раскрывает какой)."""
    import hashlib

    token_hash = hashlib.sha256(body.token.strip().encode()).hexdigest()
    row = db.scalar(select(PasswordReset).where(
        PasswordReset.token_hash == token_hash))
    if row is None or row.used_at is not None or row.expires_at < datetime.now(UTC):
        raise HTTPException(410, "reset_token_invalid")
    violations = validate_password(body.new_password)
    if violations:
        raise HTTPException(422, "; ".join(violations))
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(410, "reset_token_invalid")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    row.used_at = datetime.now(UTC)
    now = datetime.now(UTC)
    for session in db.scalars(select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None))).all():
        session.revoked_at = now
    db.add(AuditEvent(user_id=user.id, action="password.reset",
                      entity_type="user", entity_id=str(user.id)))
    db.commit()
    return {"ok": True}


def _2fa_required(user: User) -> bool:
    """О1: 2FA обязательна для роли admin И платформенного админа."""
    return user.is_platform_admin or user.role == "admin"


def _decode_mfa_token(db: Session, mfa_token: str) -> User:
    """mfa_token → пользователь (тип mfa, 5 минут; НЕ сеанс)."""
    from jwt import PyJWTError

    try:
        payload = decode_token(mfa_token)
    except PyJWTError as exc:
        raise HTTPException(401, "Invalid mfa token") from exc
    if payload.get("type") != "mfa":
        raise HTTPException(401, "Wrong token type")
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(401, "User not found")
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(401, "Token revoked")
    return user


def _issue_full_pair(db: Session, request: Request, user: User) -> TokenOut:
    """Полноценная пара + сеанс (после успешного второго фактора)."""
    from src.config import get_settings as _gs
    sid = uuid.uuid4()
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
    db.add(AuthSession(
        id=sid, user_id=user.id,
        user_agent=(request.headers.get("user-agent") or "")[:256],
        ip=(forwarded or (request.client.host if request.client else ""))[:64],
    ))
    db.flush()
    ttl_cut = datetime.now(UTC) - timedelta(days=_gs().refresh_expire_days)
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.created_at >= ttl_cut,
    )) or 1
    db.commit()
    from src.core import totp as totp_core
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
        has_2fa=totp_core.is_enabled(db, user.id),
        organizations=orgs,
    )


class MfaVerifyIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {"mfa_token": "…", "code": "123456"}}}

    mfa_token: str
    code: str = Field(min_length=6, max_length=11)  # 6 цифр или XXXX-XXXX


@router.post("/auth/mfa/verify", response_model=TokenOut)
def mfa_verify(body: MfaVerifyIn, request: Request, db: Session = Depends(get_db)):
    """Второй фактор: код TOTP или резервный (гасится). Успех — полноценная
    пара + сеанс (для pl — реестр организаций). Сотрудникам без 2FA
    токен не выдаётся (login сразу даёт пару)."""
    user = _decode_mfa_token(db, body.mfa_token)
    check_password_confirm_rate_limit(user.id)  # тот же счётчик 5/60с (§6)
    from src.core import totp as totp_core
    if not totp_core.verify_login_code(db, user.id, body.code):
        db.commit()
        raise HTTPException(401, "wrong_code")
    reset_password_confirm_rate_limit(user.id)
    db.add(AuditEvent(user_id=user.id, action="auth.mfa.verified",
                      entity_type="user", entity_id=str(user.id)))
    return _issue_full_pair(db, request, user)


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpSetupIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {"mfa_token": "…"}}}

    mfa_token: str = ""  # пусто — настройка по access-токену (руководитель)


@router.post("/auth/totp/setup", response_model=TotpSetupOut)
def totp_setup(body: TotpSetupIn | None = None, request: Request = None,
               db: Session = Depends(get_db)):
    """Настройка 2FA (руководителем): секрет + otpauth-URI. Доступ — по
    mfa_token (вход с обязательной настройкой) или access-токену;
    сотруднику (не admin и не pl) — 403: не положено (решение основателя).
    QR рендерит фронт локальной библиотекой (дух ADR-001, §5.4)."""
    from src.core import totp as totp_core

    if body and body.mfa_token:
        target = _decode_mfa_token(db, body.mfa_token)
    else:
        target = _user_from_access_header(db, request)
    if target is None:
        raise HTTPException(401, "mfa_token or Authorization required")
    if not _2fa_required(target):
        raise HTTPException(403, "2fa_not_allowed_for_employee")
    secret = totp_core.generate_secret()
    totp_core.store_secret(db, target.id, secret)
    db.add(AuditEvent(user_id=target.id, action="core.totp.setup_started",
                      entity_type="user", entity_id=str(target.id)))
    db.commit()
    return TotpSetupOut(secret=secret,
                        otpauth_uri=totp_core.otpauth_uri(secret, target.email))


class TotpConfirmIn(BaseModel):
    mfa_token: str = ""
    code: str = Field(min_length=6, max_length=6)


@router.post("/auth/totp/confirm")
def totp_confirm(body: TotpConfirmIn, request: Request,
                 db: Session = Depends(get_db)):
    """Подтверждение секрета: код из аутентификатора → 2FA включена,
    резервные коды показываются РОВНО ОДИН РАЗ. Если доступ был по
    mfa_token (вход с обязательной настройкой) — сразу выдаётся
    полноценная пара (сеанс создаётся здесь, §6)."""
    from src.core import totp as totp_core

    via_mfa = bool(body.mfa_token)
    if via_mfa:
        target = _decode_mfa_token(db, body.mfa_token)
    else:
        target = _user_from_access_header(db, request)
    if target is None:
        raise HTTPException(401, "mfa_token or Authorization required")
    check_password_confirm_rate_limit(target.id)
    secret = totp_core.load_secret(db, target.id)
    if secret is None:
        raise HTTPException(422, "totp_setup_not_started")
    if not totp_core.verify_code(secret, body.code):
        db.commit()
        raise HTTPException(401, "wrong_code")
    reset_password_confirm_rate_limit(target.id)
    codes = totp_core.confirm(db, target.id)
    db.add(AuditEvent(user_id=target.id, action="core.totp.enabled",
                      entity_type="user", entity_id=str(target.id)))
    events.publish(db, "core.totp.enabled", {"user_id": str(target.id)})
    db.commit()
    result: dict = {"backup_codes": codes, "tokens": None}
    if via_mfa:
        pair = _issue_full_pair(db, request, target)
        result["tokens"] = pair
    return result


def _user_from_access_header(db: Session, request: Request) -> User | None:
    """Текущий пользователь из заголовка Authorization (для setup/confirm
    без mfa_token — повторная настройка руководителем)."""
    from jwt import PyJWTError

    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        return None
    try:
        payload = decode_token(header[7:])
    except PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        return None
    if payload.get("ver", 0) != user.token_version:
        return None
    return user


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
    # §12.4: смена пароля снимает дедлайн временного пароля
    user.must_change_password_by = None
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
        (changed is None and user.email == "admin@example.com") or temp_issued
        or user.must_change_password_by is not None)
    payload.must_change_password_by =         user.must_change_password_by.isoformat()         if user.must_change_password_by else None
    # тенант-контекст — из клейма org (для бейджа организации в шапке)
    org = getattr(user, "token_org", None)
    company = db.get(Company, uuid.UUID(str(org))) if org else None
    payload.company_id = company.id if company else None
    payload.company_name = company.name if company else None
    payload.is_platform_admin = bool(user.is_platform_admin)
    payload.username = user.username
    return payload


# ---------- Users (admin) ----------

@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Срез пользователей своей организации. Доступ (role-delegation §6,
    Р3): admin ИЛИ обладатель rw хотя бы на один модуль (иначе руководителю
    некого выбирать при делегировании); platform-админ без org — все."""
    if user.role != "admin" and not _can_delegate(user, db):
        raise HTTPException(403, "Requires rw on at least one module")
    org = getattr(user, "token_org", None) or user.company_id
    if getattr(user, "token_pl", False) and not org:
        return db.scalars(select(User)).all()
    if org is None:
        raise HTTPException(403, "no_company_context")
    return db.scalars(select(User).where(
        User.company_id == uuid.UUID(str(org)))).all()


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
    # делегирование §6: смена роли/деактивация → каскадный отзыв выдач,
    # опиравшихся на утраченное rw (в той же транзакции)
    if body.role is not None or body.is_active is not None:
        db.flush()  # каскад должен видеть изменения роли/строк этой транзакции
        for module in MODULES:
            _cascade_revoke(db, user.id, module,
                            "role change" if body.role is not None
                            else "deactivation")
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password")
def org_reset_password(user_id: uuid.UUID, admin: AdminUser,
                       db: Session = Depends(get_db),
                       scoped: uuid.UUID = Depends(current_company)):
    """Сброс пароля сотрудника своей организации (§7.4): админ организации,
    цель — сотрудник своей org и НЕ себя (свой — через email-ссылку или
    платформу, иначе обнуляется смысл 2FA при захваченной сессии).
    Временный пароль — показ один раз; token_version+=1, ревок сеансов."""
    target = db.get(User, user_id)
    if target is None or target.company_id != scoped or target.id == admin.id:
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
        user_id=admin.id, action="org.password.reset",
        entity_type="user", entity_id=str(target.id),
        payload={"email": target.email}))
    db.add(AuditEvent(
        user_id=admin.id, action="platform.user.temp_password",
        entity_type="user", entity_id=str(target.id), payload={}))
    db.commit()
    return {"temp_password": temp_password, "must_change_password": True}


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
        # дедлайн настройки 2FA: 7 дней с создания учётки (этап D-ревью)
        totp_setup_deadline=datetime.now(UTC) + timedelta(days=7),
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
        "company_id": str(company.id), "name": company.name,
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
                   {"company_id": str(company.id), "name": company.name})
    db.commit()
    db.refresh(company)
    users_count = db.scalar(select(func.count()).select_from(User).where(
        User.company_id == company.id)) or 0
    return OrgOut(id=company.id, name=company.name, inn=company.inn,
                  is_active=company.is_active, users_count=int(users_count),
                  created_at=company.created_at)


@router.post("/platform/users/{user_id}/totp/reset")
def platform_totp_reset(user_id: uuid.UUID, admin: PlatformAdmin,
                        db: Session = Depends(get_db)):
    """Сброс 2FA платформой (потерян телефон): секрет и резервные коды
    удаляются; обязательность сохраняется — следующий вход руководителя
    запустит настройку заново. Аудит core.totp.reset."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    from src.core import totp as totp_core

    totp_core.reset(db, target.id)
    db.add(AuditEvent(
        user_id=admin.id, action="platform.totp.reset",
        entity_type="user", entity_id=str(target.id),
        payload={"email": target.email}))
    events.publish(db, "core.totp.reset", {"user_id": str(target.id)})
    db.commit()
    return {"ok": True}


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
    # личные выдачи делегирования (role-delegation §6); permissions —
    # эффективный максимум, granted — только надстройка
    granted: dict[str, str] = {}

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
    """Эффективные права = max(роль, личные выдачи) — ADR-002: payload
    только расширяется (granted — личная надстройка делегирования)."""
    from src.core.auth import effective_module_level

    role = db.get(Role, user.role)
    effective = {module: effective_module_level(db, user, module)
                 for module in MODULES}
    personal = {row.module: row.level for row in db.scalars(
        select(UserPermission).where(UserPermission.user_id == user.id)).all()}
    return PermissionsOut(
        role={"key": user.role, "name": role.name if role else user.role},
        permissions=effective,
        granted=personal,
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
