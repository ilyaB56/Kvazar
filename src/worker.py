"""Celery-приложение: очереди integrations (синхронизации) и default."""

from celery import Celery

from src.config import get_settings

settings = get_settings()

celery_app = Celery(
    "erp",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.modules.integrations.tasks"],
)

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
    },
    worker_hijack_root_logger=False,
)
