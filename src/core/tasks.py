"""Фоновые задачи ядра: бэкапы по расписанию и проверка обновлений."""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.core.backup import create_backup, verify_backup
from src.core.models import Backup
from src.db import SessionLocal
from src.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def backup_task(kind: str = "scheduled") -> dict:
    backup = create_backup(kind=kind)
    logger.info("backup created: %s (%s)", backup.file_name, kind)
    return {"id": str(backup.id), "file_name": backup.file_name, "status": backup.status}


@celery_app.task
def verify_backup_task(backup_id: str) -> dict:
    backup = verify_backup(backup_id)
    return {"id": str(backup.id), "status": backup.status}


@celery_app.task(name="src.core.tasks.verify_latest_backup_task")
def verify_latest_backup_task() -> dict:
    """Ежемесячная проверка самого свежего бэкапа («непроверенный бэкап — не бэкап»)."""
    db = SessionLocal()
    try:
        latest = db.scalar(select(Backup).order_by(
            Backup.created_at.desc(), Backup.id.desc()
        ).limit(1))
        if latest is None:
            logger.warning("monthly verify: no backups found")
            return {"skipped": True}
    finally:
        db.close()
    backup = verify_backup(latest.id)
    return {"id": str(backup.id), "status": backup.status}


@celery_app.task
def check_update_task() -> dict:
    """Раз в 24 ч: манифест по UPDATE_MANIFEST_URL, подпись, версии, settings."""
    from src.core.update.check import UpdateCheckError, check_update

    try:
        return check_update()
    except UpdateCheckError as exc:
        # непроверяемое — игнорируется с записью в журнал (ADR-004)
        logger.warning("update check failed: %s", exc)
        return {"error": str(exc)}
