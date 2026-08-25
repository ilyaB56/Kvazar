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
from src.core.models import ApiToken, User
from src.db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

_settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user_id: uuid.UUID, role: str, ver: int = 0) -> str:
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
    return jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: uuid.UUID, ver: int = 0) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "ver": ver,
        "exp": now + timedelta(days=_settings.refresh_expire_days),
        "iat": now,
    }
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
        return ApiPrincipal(row)
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    # security-p0 п.3: ver в токене должен совпадать с users.token_version
    # (смена пароля инвалидирует все ранее выданные токены)
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revoked")
    return user


CurrentUser = Annotated[User | ApiPrincipal, Depends(get_current_user)]


def get_current_human(user: CurrentUser) -> User:
    """Только живой пользователь (JWT): профиль, смена пароля — не для токенов."""
    if not isinstance(user, User):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available for API tokens")
    return user


HumanUser = Annotated[User, Depends(get_current_human)]


def require_role(*roles: str):
    """Зависимость для защиты эндпоинтов: Depends(require_role("admin"))."""

    def checker(user: CurrentUser) -> User:
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
