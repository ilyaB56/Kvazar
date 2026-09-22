"""Самообслуживание клиентов (блок 2): публичная регистрация.

POST /auth/signup → заявка pending + письмо со ссылкой подтверждения
(24 ч, одноразовый токен; sha256 в БД, открытый текст — только в письме
через Fernet). Verify → уведомление платформенному админу; одобрение —
эндпоинт платформы (организация + админ с паролём из заявки + сиды).
Письма — через ПЛАТФОРМЕННЫЙ SMTP (как восстановление пароля, Р4)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core.crypto import decrypt_str, encrypt_str
from src.core.models import Setting, SignupRequest
from src.db import SessionLocal

logger = logging.getLogger(__name__)

TOKEN_TTL = timedelta(hours=24)


def _smtp() -> tuple | None:
    """Платформенный SMTP-коннектор (company_id IS NULL) или None."""
    from src.modules.integrations import models as im
    from src.modules.integrations.connectors.builtin import registry
    from src.modules.integrations.crypto import decrypt_dict

    db = SessionLocal()
    try:
        # детерминированно: свежейший платформенный SMTP (в реестре могут
        # остаться старые с мёртвыми адресами)
        conn = db.scalar(select(im.Connection).where(
            im.Connection.connector_code == "smtp",
            im.Connection.company_id.is_(None),
            im.Connection.is_active.is_(True))
            .order_by(im.Connection.created_at.desc()).limit(1))
        if conn is None:
            return None
        return registry.build(conn.connector_code, conn.config,
                              decrypt_dict(conn.credentials_enc))
    finally:
        db.close()


def _web_url(db) -> str:
    row = db.scalar(select(Setting).where(
        Setting.key == "web_url", Setting.company_id.is_(None)))
    return (row.value if row and row.value else "http://localhost:8080")


def _send(to: str, subject: str, text: str) -> bool:
    smtp = _smtp()
    if smtp is None:
        logger.warning("signup: platform SMTP not configured — email skipped")
        return False
    result = smtp.push(params={"to": to, "subject": subject, "text": text})
    if not result.ok:
        logger.warning("signup: email failed: %s", str(result.error)[:200])
    return result.ok


def create_or_refresh(db, *, company_name: str, contact_name: str,
                      email: str, password_hash: str, ip: str) -> None:
    """Новая заявка или перевыпуск токена существующей pending/verified.

    Одно письмо на email: повторная регистрация обновляет заявку."""
    token = secrets.token_urlsafe(32)
    row = db.scalar(select(SignupRequest).where(
        SignupRequest.email == email,
        SignupRequest.status.in_(("pending", "verified")),
    ))
    if row is None:
        row = SignupRequest(email=email)
        db.add(row)
    row.company_name = company_name
    row.contact_name = contact_name
    row.password_hash = password_hash
    row.token_hash = hashlib.sha256(token.encode()).hexdigest()
    row.token_enc = encrypt_str(token)
    row.expires_at = datetime.now(UTC) + TOKEN_TTL
    row.verified_at = None
    row.status = "pending"
    row.created_ip = ip
    db.flush()
    db.commit()
    # токен в событиях шины не публикуем (как в password_resets)
    _send(email, "Подтверждение регистрации ERP «Квазар»",
          "Здравствуйте!\n\n"
          f"Заявка на подключение компании «{company_name}».\n"
          "Подтвердите email по ссылке (действительна 24 часа, "
          "одноразовая):\n\n"
          f"{_web_url(db)}/signup/verify?token={token}\n\n"
          "Если это были не вы — просто проигнорируйте письмо.\n")


def verify(db, token: str) -> bool:
    """Одноразовое подтверждение: pending+живой токен → verified +
    уведомление платформенному админу. False — нет/истёк/использован."""
    row = db.scalar(select(SignupRequest).where(
        SignupRequest.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if row is None or row.status != "pending":
        return False
    if datetime.now(UTC) > row.expires_at:
        return False
    row.verified_at = datetime.now(UTC)
    row.status = "verified"
    db.commit()
    notify_platform_admin(db, row)
    return True


def notify_platform_admin(db, row: SignupRequest) -> None:
    """«Новая регистрация: Компания X, email Y» + ссылка на «Ожидающие»."""
    target_row = db.scalar(select(Setting).where(
        Setting.key == "platform_notify_email", Setting.company_id.is_(None)))
    target = (target_row.value if target_row and target_row.value
              else "admin@example.com")
    _send(target, "Новая регистрация ERP «Квазар»",
          "Новая подтверждённая регистрация:\n\n"
          f"Компания: {row.company_name}\n"
          f"Контакт: {row.contact_name or '—'}\n"
          f"Email: {row.email}\n\n"
          f"Одобрить/отклонить: {_web_url(db)}/select-org "
          "(блок «Ожидающие»)\n")


def recovery_token(row: SignupRequest) -> str:
    """Токен из Fernet — для тестов/диагностики (в письме тот же)."""
    return decrypt_str(row.token_enc)
