"""Rate limit логина (security-p0 п.2): Redis-счётчик неудачных попыток.

Ключ login_attempts:{ip}, окно 60 с, порог 5 неудач; превышение — 429 c
Retry-After. Успешный вход сбрасывает счётчик. Redis недоступен → fail-open
(логин работает, ошибка пишется в лог один раз): оффлайн-коробка важнее
блокировки входа.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from src.config import get_settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60
MAX_ATTEMPTS = 5

_redis = None
_warned = False


def _client():
    global _redis
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1
        )
    return _redis


def client_ip(request: Request) -> str:
    """IP клиента: за nginx/vite-прокси — X-Forwarded-For / X-Real-IP."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )


def check_login_rate_limit(request: Request) -> None:
    """Вызывается в начале POST /auth/login; 429 при превышении порога."""
    global _warned
    try:
        client = _client()
        key = f"login_attempts:{client_ip(request)}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, WINDOW_SECONDS)
        if count > MAX_ATTEMPTS:
            ttl = max(client.ttl(key), 1)
            raise HTTPException(
                429, "Too many login attempts", headers={"Retry-After": str(ttl)}
            )
    except HTTPException:
        raise
    except Exception:
        if not _warned:
            logger.warning("login rate limit unavailable, fail-open", exc_info=True)
            _warned = True


def reset_login_rate_limit(request: Request) -> None:
    """Успешный вход: счётчик попыток сбрасывается."""
    global _warned
    try:
        _client().delete(f"login_attempts:{client_ip(request)}")
    except Exception:
        if not _warned:
            logger.warning("login rate limit unavailable, fail-open", exc_info=True)
            _warned = True


def check_password_confirm_rate_limit(user_id) -> None:
    """Счётчик подтверждений паролем (sessions-security §2.2): разрушительные
    действия над сеансами (logout-others/revoke) — 5 попыток/60с по user_id,
    429 c Retry-After. Ключ по пользователю, а не IP: брутфорс под одним
    аккаунтом не обходится сменой адреса. Redis недоступен → fail-open."""
    global _warned
    try:
        client = _client()
        key = f"pw_confirm_attempts:{user_id}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, WINDOW_SECONDS)
        if count > MAX_ATTEMPTS:
            ttl = max(client.ttl(key), 1)
            raise HTTPException(
                429, "Too many attempts", headers={"Retry-After": str(ttl)}
            )
    except HTTPException:
        raise
    except Exception:
        if not _warned:
            logger.warning("password-confirm rate limit unavailable, fail-open",
                           exc_info=True)
            _warned = True


def reset_password_confirm_rate_limit(user_id) -> None:
    """Успешное подтверждение: счётчик сбрасывается."""
    global _warned
    try:
        _client().delete(f"pw_confirm_attempts:{user_id}")
    except Exception:
        if not _warned:
            logger.warning("password-confirm rate limit unavailable, fail-open",
                           exc_info=True)
            _warned = True
