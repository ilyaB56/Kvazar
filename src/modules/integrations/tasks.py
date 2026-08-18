"""Фоновые задачи синхронизации (Celery)."""

from __future__ import annotations

import logging

from src.core.events import dispatch_outbox, publish
from src.db import SessionLocal
from src.modules.integrations import models as m
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict
from src.worker import celery_app

logger = logging.getLogger(__name__)


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
def dispatch_outbox_task() -> int:
    """Плановая рассылка накопившихся событий из outbox (вызывается beat'ом)."""
    db = SessionLocal()
    try:
        return dispatch_outbox(db)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def run_job(self, job_id: str) -> dict:
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
            if transformed:
                publish(db, "integration.data.fetched",
                        {"job": job.name, "items": transformed[:100]})
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
            db.commit()
            return {"ok": False, "error": str(exc)}
        raise
    finally:
        db.close()
