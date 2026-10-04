"""JWT-аутентификация и RBAC ядра."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core.models import ApiToken, AuthSession, Company, RolePermission, User
from src.db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

_settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user_id: uuid.UUID, role: str, ver: int = 0,
                        sid: uuid.UUID | str | None = None,
                        org: uuid.UUID | str | None = None,
                        pl: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "ver": ver,
        "exp": now + timedelta(minutes=_settings.jwt_expire_minutes),
        "iat": now,
    }
    if sid is not None:
        # sessions-security §2.1: клейм сеанса — маркировка «текущий» и
        # мгновенный 401 по отзыву сессии (logout-others/revoke)
        payload["sid"] = str(sid)
    if org is not None:
        # multitenancy §5.2: контекст организации в токене (не users.company_id
        # — переключение супер-админа работает без правки пользователя)
        payload["org"] = str(org)
    if pl:
        payload["pl"] = True
    return jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")


def create_mfa_token(user_id: uuid.UUID) -> str:
    """Короткоживущий токен второго фактора (multitenancy §7.1): 5 минут,
    НЕ сеанс (AuthSession не создаётся), без org/pl; выдаётся вместо пары
    при включённой 2FA и при обязательной незавершённой настройке."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "mfa",
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=5),
        "iat": now,
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: uuid.UUID, ver: int = 0,
                         sid: uuid.UUID | str | None = None,
                         org: uuid.UUID | str | None = None,
                         pl: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "ver": ver,
        "exp": now + timedelta(days=_settings.refresh_expire_days),
        "iat": now,
    }
    if sid is not None:
        payload["sid"] = str(sid)
    if org is not None:
        payload["org"] = str(org)
    if pl:
        payload["pl"] = True
    return jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _settings.jwt_secret, algorithms=["HS256"])


class ApiPrincipal:
    """Прокси API-токена вместо User: ролевые проверки и аудит работают так же.

    Действия записываются от имени владельца токена (owner), поэтому created_by
    и аудит ссылаются на живого пользователя (FK users). Пользовательские
    auth-эндпоинты (/auth/me, смена пароля) токену запрещены.
    """

    def __init__(self, token: ApiToken):
        self.id = token.owner_user_id or token.id
        self.token_id = token.id
        self.role = token.role
        self.is_active = token.is_active
        self.name = token.name


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    if x_api_token:
        # служебный токен: sha256 сравнивается с token_hash (showcase-chain, этап A)
        token_hash = sha256(x_api_token.encode()).hexdigest()
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if row is None or not row.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API token")
        row.last_used_at = datetime.now(UTC)
        db.commit()
        principal = ApiPrincipal(row)
        # multitenancy §2 п.2/§5.3: организация токена (задана при создании
        # в контексте org), иначе — организации владельца
        principal.token_org = str(row.company_id) if row.company_id else None
        principal.token_pl = False
        if principal.token_org is None and row.owner_user_id:
            owner = db.get(User, row.owner_user_id)
            if owner is not None:
                principal.token_org = str(owner.company_id) if owner.company_id else None
                principal.token_pl = bool(owner.is_platform_admin)
        return principal
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    # mfa_token — только для /auth/mfa/* эндпоинтов (проверяется там отдельно)
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    # security-p0 п.3: ver в токене должен совпадать с users.token_version
    # (смена пароля инвалидирует все ранее выданные токены)
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revoked")
    # sessions-security §2.1: сеанс отозван (logout-others/revoke) — 401
    # сразу, не дожидаясь exp access-токена (PK-lookup, дёшево)
    sid = payload.get("sid")
    if sid:
        session = db.get(AuthSession, uuid.UUID(sid))
        if session is not None and session.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
    # multitenancy: контекст из токена (атрибут, не колонка)
    user.token_org = payload.get("org")
    user.token_pl = bool(payload.get("pl"))
    # §12.4: дедлайн смены временного пароля истёк → мутации запрещены
    # (password_expired); чтение доступно — войти и сменить пароль можно
    if user.must_change_password_by is not None:
        user.password_expired = user.must_change_password_by < datetime.now(UTC)
    else:
        user.password_expired = False
    return user


CurrentUser = Annotated[User | ApiPrincipal, Depends(get_current_user)]


def get_current_human(user: CurrentUser) -> User:
    """Только живой пользователь (JWT): профиль, смена пароля — не для токенов."""
    if not isinstance(user, User):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available for API tokens")
    return user


HumanUser = Annotated[User, Depends(get_current_human)]


def require_role(*roles: str):
    """Зависимость для защиты эндпоинтов: Depends(require_role("admin")).
    Мутационные роли (admin/user) при истёкшем дедлайне пароля — 403
    password_expired (§12.4): гвард мутаций, чтение живёт на ro-путях."""

    def checker(user: CurrentUser) -> User:
        if getattr(user, "password_expired", False):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "password_expired")
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {roles}")
        return user

    return checker


# Спека security-p0, п.1: mutating-эндпоинты — минимум user (readonly только GET);
# конфигурация интеграций — admin. Аннотации ниже — сахар над этими зависимостями.
require_write = require_role("admin", "user")
require_admin = require_role("admin")

WriteUser = Annotated[User, Depends(require_write)]
AdminUser = Annotated[User, Depends(require_admin)]


# ---------- Мультитенантность (multitenancy-spec §7.2) ----------

def _require_platform_admin(user: CurrentUser) -> User:
    if not getattr(user, "token_pl", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Platform admin context required")
    return user


PlatformAdmin = Annotated[User, Depends(_require_platform_admin)]
"""Платформенный контекст: только is_platform_admin (клейм pl)."""


def current_company(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> uuid.UUID:
    """Тенант-контекст: org из JWT (не users.company_id!). Нет org —
    платформенный контекст без выбора → 403; организация неактивна → 403.
    Этап A — зависимость ядра; этап B переводит модули на неё."""
    org = getattr(user, "token_org", None)
    if not org:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no_company_context")
    company = db.get(Company, uuid.UUID(str(org)))
    if company is None or not company.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "organization_disabled")
    return company.id


CompanyScoped = Annotated[uuid.UUID, Depends(current_company)]
"""company_id контекста для фильтров сервисного слоя (первый рубеж изоляции)."""


# ---------- Роли и права (редизайн §6.3) ----------

# инструменты аналитика/разработчика (devtools-spec §5): уровни как у
# модулей — ro = чтение, rw = мутации; встроенным ролям не выдаются
TOOL_MODULES = ("table_browser", "maint_views", "devtools")
MODULES = ("accounting", "crm", "integrations", "ai", "system") + TOOL_MODULES


def module_level(db: Session, role_key: str, module: str) -> str:
    """Уровень доступа роли к модулю: 'rw' | 'ro' | 'none'.

    admin — неизменяемая роль с полным доступом (спека §6.1), строки прав
    для неё есть в сиде, но проверка не зависит от их наличия.
    """
    if role_key == "admin":
        return "rw"
    level = db.scalar(
        select(RolePermission.level).where(
            RolePermission.role_key == role_key, RolePermission.module == module
        )
    )
    return level or "none"


_LEVEL_ORDER = {"none": 0, "ro": 1, "rw": 2}


def effective_module_level(db: Session, user, module: str) -> str:
    """Эффективный уровень пользователя на модуль (role-delegation §5):
    admin → rw; иначе max(уровень роли, личная выдача) по решётке
    none < ro < rw. Надстройка только добавляет доступ (понижение ниже
    роли — вне v1). Права читаются из БД — выдача применяется без
    перелогина."""
    if user.role == "admin":
        return "rw"
    role_level = module_level(db, user.role, module)
    from src.core.models import UserPermission

    personal = db.scalar(select(UserPermission.level).where(
        UserPermission.user_id == user.id, UserPermission.module == module))
    best = role_level
    if personal is not None and _LEVEL_ORDER[personal] > _LEVEL_ORDER[best]:
        best = personal
    return best


def require_module(module: str, level: str = "rw"):
    """Зависимость доступа к модулю (§6.3): level 'ro' — только чтение,
    'rw' — полный; 'none'/нет строки — 403. Эффективный уровень =
    max(роль, личное делегирование) — читается из БД на каждом запросе,
    смена прав применяется сразу.

    ApiPrincipal (X-API-Token): личных выдач у токена нет — ходит по
    роли токена (осознанная граница v1, role-delegation §5)."""

    def checker(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> User:
        if isinstance(user, ApiPrincipal):
            actual = module_level(db, user.role, module)
        else:
            actual = effective_module_level(db, user, module)
        # §12.4: rw-запрос = мутация; дедлайна пароля истёк — 403
        if level == "rw" and getattr(user, "password_expired", False):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "password_expired")
        if actual == "none" or (level == "rw" and actual != "rw"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires {module}:{level}, effective '{actual}'",
            )
        return user

    return checker
