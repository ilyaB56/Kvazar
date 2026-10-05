"""Периодика уведомлений ядра (notifications-spec §6.2).

totp_deadline_reminder — ежедневно 06:00; backup_stale_check — 06:15.
Идемпотентность — dedup_key с дневным окном (UNIQUE user_id+dedup_key).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core.models import Backup, User, UserTotp
from src.core.notifications.service import notify
from src.core.tasks import celery_app
from src.db import SessionLocal

logger = logging.getLogger(__name__)

_TOTP_STEPS = (7, 3, 1, 0)
_DAYS_WORD = {7: "7 дней", 3: "3 дня", 1: "1 день", 0: "сегодня"}


@celery_app.task(name="src.core.notifications.tasks.totp_deadline_reminder")
def totp_deadline_reminder() -> dict:
    """Личное напоминание о дедлайне 2FA за 7/3/1/0 дней (если не включена)."""
    db = SessionLocal()
    created = 0
    try:
        today = datetime.now(UTC).date()
        users = db.scalars(select(User).where(
            User.is_active.is_(True),
            User.totp_setup_deadline.is_not(None),
        )).all()
        for user in users:
            totp = db.scalar(select(UserTotp).where(UserTotp.user_id == user.id))
            if totp is not None and totp.enabled_at is not None:
                continue  # 2FA включена — не напоминаем
            deadline = user.totp_setup_deadline
            deadline = deadline.date() if isinstance(deadline, datetime) else deadline
            days = (deadline - today).days
            if days not in _TOTP_STEPS:
                continue
            if days < 0:
                continue
            title = ("Настройте двухфакторную аутентификацию — "
                     f"осталось {_DAYS_WORD[days]}" if days > 0 else
                     "Последний день: настройте двухфакторную аутентификацию")
            created += notify(
                db, company_id=user.company_id, kind="totp_deadline",
                audience="user", user_id=user.id, title=title,
                body="После дедлайна вход будет заблокирован до настройки 2FA.",
                entity_id=str(user.id),
                dedup_key=f"totp:{user.id}:{days}",
            )
        db.commit()
    finally:
        db.close()
    logger.info("totp_deadline_reminder: %d уведомлений", created)
    return {"created": created}


@celery_app.task(name="src.core.notifications.tasks.backup_stale_check")
def backup_stale_check() -> dict:
    """Нет успешного бэкапа свежее 25 ч или ошибка за 25 ч → платформенным админам."""
    db = SessionLocal()
    created = 0
    try:
        cutoff = datetime.now(UTC) - timedelta(hours=25)
        latest_ok = db.scalar(select(Backup).where(
            Backup.status.in_(["created", "verified"]),
            Backup.created_at >= cutoff,
        ).order_by(Backup.created_at.desc()).limit(1))
        if latest_ok is not None:
            return {"created": 0}
        detail = "Последний успешный бэкап старше 25 часов"
        failed = db.scalar(select(Backup).where(
            Backup.status == "failed", Backup.created_at >= cutoff,
        ).order_by(Backup.created_at.desc()).limit(1))
        if failed is not None:
            detail = f"Последний бэкап завершился с ошибкой: {failed.file_name}"
        bucket = datetime.now(UTC).strftime("%Y-%m-%d")
        created = notify(
            db, company_id=None, kind="backup_stale",
            audience="platform_admins", title="Нет свежего бэкапа установки",
            body=detail, dedup_key=f"backup:{bucket}",
        )
        db.commit()
    finally:
        db.close()
    logger.info("backup_stale_check: %d уведомлений", created)
    return {"created": created}
