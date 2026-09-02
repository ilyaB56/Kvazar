"""Фоновые задачи синхронизации (Celery) + cron-планировщик заданий."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from src.core.events import dispatch_outbox, publish
from src.db import SessionLocal
from src.modules.integrations import models as m
from src.modules.integrations import scheduler
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict
from src.worker import celery_app

logger = logging.getLogger(__name__)

_redis_client = None
# пока задание выполняется, повторная постановка в очередь не допускается (этап B).
# Флаг хранит таймстамп старта: если воркер убит без finally (рестарт движка),
# протухший флаг снимается сам, а не ждёт весь TTL
LOCK_TTL_SECONDS = 1800
LOCK_STALE_SECONDS = 600


def _redis():
    global _redis_client
    if _redis_client is None:
        import redis

        from src.config import get_settings

        _redis_client = redis.Redis.from_url(get_settings().redis_url)
    return _redis_client


def _acquire_job_lock(job_id: str) -> bool:
    import time

    key = f"sync_job_in_progress:{job_id}"
    client = _redis()
    if client.set(key, str(time.time()), nx=True, ex=LOCK_TTL_SECONDS):
        return True
    started = client.get(key)
    if started is not None and time.time() - float(started) > LOCK_STALE_SECONDS:
        # протухший флаг (воркер погиб) — снимаем и захватываем заново
        client.delete(key)
        return bool(client.set(key, str(time.time()), nx=True, ex=LOCK_TTL_SECONDS))
    return False


@celery_app.task
def run_due_sync_jobs_task() -> dict:
    """Раз в минуту (beat): поставить в очередь задания, чей cron наступил.

    Анти-дубли: условный UPDATE last_run_at (гонки двух тиков/воркеров
    исключены — rowcount=0 у проигравшего) + Redis-флаг in_progress в run_job.
    """
    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        jobs = db.scalars(select(m.SyncJob).where(
            m.SyncJob.is_active.is_(True),
            m.SyncJob.cron != "",
        )).all()
        fired: list[str] = []
        for job in jobs:
            if not scheduler.is_due(job.cron, job.last_run_at, job.created_at, now):
                continue
            claimed = db.execute(
                update(m.SyncJob)
                .where(
                    m.SyncJob.id == job.id,
                    or_(
                        m.SyncJob.last_run_at.is_(None),
                        m.SyncJob.last_run_at < now - timedelta(seconds=scheduler.ANTI_DUPLICATE_SECONDS),
                    ),
                )
                .values(last_run_at=now)
            )
            db.commit()
            if claimed.rowcount:
                run_job.delay(str(job.id))
                fired.append(job.name)
        if fired:
            logger.info("scheduler fired: %s", fired)
        return {"fired": fired}
    finally:
        db.close()


def apply_mapping(data, mapping: m.FieldMapping | None):
    """Простое преобразование полей: source->target + rename-трансформации."""
    if mapping is None:
        return data
    pairs = list(zip(mapping.source_fields, mapping.target_fields))
    result = {}
    for source, target in pairs:
        value = data.get(source) if isinstance(data, dict) else None
        transform = (mapping.transformations or {}).get(source)
        if transform == "upper" and isinstance(value, str):
            value = value.upper()
        elif transform == "to_float" and value is not None:
            try:
                value = float(str(value).replace(",", "."))
            except ValueError:
                pass
        result[target] = value
    return result or data


@celery_app.task
def notify_task(rule_id: str, payload: dict) -> bool:
    """Отправка одного уведомления — в воркере: блокирующий httpx не должен
    останавливать event loop API (dispatch_outbox вызывается и в api)."""
    from src.modules.integrations.notify import send_notification

    db = SessionLocal()
    try:
        rule = db.get(m.NotificationRule, rule_id)
        if rule is None or not rule.is_active:
            return False
        return send_notification(rule, payload)
    finally:
        db.close()


@celery_app.task
def recipe_task(recipe_id: str, payload: dict) -> bool:
    """Исполнение действия рецепта — в воркере (блокирующий api_call)."""
    db = SessionLocal()
    try:
        recipe = db.get(m.Recipe, recipe_id)
        if recipe is None or not recipe.is_published:
            return False
        from src.modules.integrations.recipes_executor import execute_recipe_action

        return execute_recipe_action(recipe, payload)
    finally:
        db.close()


@celery_app.task
def dispatch_outbox_task() -> int:
    """Плановая рассылка накопившихся событий из outbox (вызывается beat'ом)."""
    db = SessionLocal()
    try:
        return dispatch_outbox(db)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def run_job(self, job_id: str) -> dict:
    if not _acquire_job_lock(job_id):
        logger.info("job %s already running, skip duplicate", job_id)
        return {"skipped": True, "reason": "already running"}
    db = SessionLocal()
    try:
        job = db.get(m.SyncJob, job_id)
        if job is None or not job.is_active:
            return {"skipped": True}
        connection = db.get(m.Connection, job.connection_id)
        connector = connector_registry.build(
            connection.connector_code, connection.config, decrypt_dict(connection.credentials_enc)
        )
        mapping = db.get(m.FieldMapping, job.mapping_id) if job.mapping_id else None

        if job.direction == "fetch":
            result = connector.fetch(job.endpoint)
            items = result.data if isinstance(result.data, list) else [result.data]
            transformed = [apply_mapping(item, mapping) for item in items if item is not None]
            if transformed and job.emit_event != "integration.data.fetched":
                # спец-событие задания: payload = сам результат + source/job
                # (контракт события главнее generic-формата, ADR-002)
                for item in transformed:
                    if isinstance(item, dict):
                        publish(db, job.emit_event,
                                {**item, "source": connection.connector_code, "job": job.name})
            elif transformed:
                publish(db, job.emit_event, {"job": job.name, "items": transformed[:100]})
        else:
            # push: полезная нагрузка пока задаётся вручную через API/mapping
            result = connector.push(job.endpoint, {})

        run = m.SyncRun(
            sync_job_id=job.id,
            status="success" if result.ok else "error",
            items_in=len(transformed) if job.direction == "fetch" else 1,
            items_out=len(transformed) if job.direction == "fetch" else (1 if result.ok else 0),
            payload={"error": result.error} if not result.ok else {},
            error=result.error,
        )
        db.add(run)
        db.commit()
        dispatch_outbox(db)
        if not result.ok:
            raise self.retry(exc=RuntimeError(result.error))
        return {"ok": True}
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            db.rollback()
            db.add(m.SyncRun(sync_job_id=job_id, status="error", error=str(exc)))
            # финальный провал: событие для уведомлений (showcase-chain, этап D)
            job = db.get(m.SyncJob, job_id)
            publish(db, "integration.sync.failed",
                    {"job": job.name if job else job_id, "error": str(exc)[:500]})
            db.commit()
            return {"ok": False, "error": str(exc)}
        raise
    finally:
        db.close()
        _redis().delete(f"sync_job_in_progress:{job_id}")
