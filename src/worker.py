"""Celery-приложение: очереди integrations (синхронизации) и default."""

from celery import Celery
from celery.schedules import crontab

from src.config import get_settings

settings = get_settings()

celery_app = Celery(
    "erp",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.modules.integrations.tasks", "src.core.tasks"],
)


def _parse_schedule(hhmm: str) -> crontab:
    """BACKUP_SCHEDULE='HH:MM' → crontab; читается при старте beat."""
    hour, minute = hhmm.strip().split(":")
    return crontab(hour=int(hour), minute=int(minute))


celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "src.modules.integrations.tasks.*": {"queue": "integrations"},
    },
    beat_schedule={
        # Диспетчер outbox каждые 30 секунд — надёжная доставка событий.
        "dispatch-outbox": {
            "task": "src.modules.integrations.tasks.dispatch_outbox_task",
            "schedule": 30.0,
        },
        # Cron-планировщик sync jobs (showcase-chain, этап B)
        "run-due-sync-jobs": {
            "task": "src.modules.integrations.tasks.run_due_sync_jobs_task",
            "schedule": crontab(minute="*"),
        },
        # Ежедневный бэкап в BACKUP_SCHEDULE (перезапуск beat подхватит новое значение)
        "daily-backup": {
            "task": "src.core.tasks.backup_task",
            "schedule": _parse_schedule(settings.backup_schedule),
        },
        # Ежемесячная проверка целостности самого свежего бэкапа (1-е число, 04:00)
        "monthly-backup-verify": {
            "task": "src.core.tasks.verify_latest_backup_task",
            "schedule": crontab(day_of_month=1, hour=4, minute=0),
        },
        # Проверка обновлений раз в 24 ч (ADR-004: уведомление-pull, не push)
        "daily-update-check": {
            "task": "src.core.tasks.check_update_task",
            "schedule": crontab(hour=5, minute=0),
        },
    },
    worker_hijack_root_logger=False,
)
