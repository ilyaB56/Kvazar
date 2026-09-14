"""Обработчик восстановления пароля (multitenancy §7.5, этап D).

Подписка на core.password.reset_requested: токен в событии НЕТ (неутечка
через шину) — обработчик читает активную строку erp_core.password_resets
(контракт ядра, как users/contacts) и восстанавливает токен из Fernet.
Письмо со ссылкой {web_url}/reset-password?token=… — через ПЛАТФОРМЕННЫЙ
SMTP (connections.company_id IS NULL, Р4); нет SMTP / ошибка — warn,
повторная отправка — повторным forgot-password (токен живёт час)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from src.core.events import on
from src.db import SessionLocal

logger = logging.getLogger(__name__)


@on("core.password.reset_requested")
def send_reset_email(payload: dict) -> None:
    from src.core.crypto import decrypt_str
    from src.core.models import PasswordReset, Setting, User
    from src.modules.integrations import models as im

    user_id = payload.get("user_id")
    if not user_id:
        return
    db = SessionLocal()
    try:
        user = db.get(User, uuid.UUID(str(user_id)))
        row = db.scalar(select(PasswordReset).where(
            PasswordReset.user_id == user.id,
            PasswordReset.used_at.is_(None),
        ).order_by(PasswordReset.created_at.desc()).limit(1))
        if user is None or row is None:
            logger.warning("password-recovery: no active token for %s", user_id)
            return
        token = decrypt_str(row.token_enc)

        web_url_row = db.scalar(select(Setting).where(
            Setting.key == "web_url", Setting.company_id.is_(None)))
        web_url = (web_url_row.value if web_url_row and web_url_row.value
                   else "http://localhost:8080")
        link = f"{web_url}/reset-password?token={token}"

        conn = db.scalar(select(im.Connection).where(
            im.Connection.connector_code == "smtp",
            im.Connection.company_id.is_(None),
            im.Connection.is_active.is_(True)))
        if conn is None:
            logger.warning("password-recovery: platform SMTP not configured "
                           "(connection smtp with company_id NULL) — skip")
            return
        from .connectors.builtin import registry as connector_registry
        from .crypto import decrypt_dict

        connector = connector_registry.build(
            conn.connector_code, conn.config, decrypt_dict(conn.credentials_enc))
        result = connector.push(params={
            "to": user.email,
            "subject": "Восстановление пароля ERP «Квазар»",
            "text": (
                "Здравствуйте!\n\n"
                "Запрошено восстановление пароля вашей учётной записи.\n"
                f"Ссылка (действительна 1 час, одноразовая):\n\n{link}\n\n"
                "Если это были не вы — просто проигнорируйте письмо.\n"
            ),
        })
        if not result.ok:
            logger.warning("password-recovery: email failed: %s",
                           str(result.error)[:200])
    finally:
        db.close()
