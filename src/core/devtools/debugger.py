"""Отладчик (devtools-spec §8): журналы, трассировка, диагностика.

Только чтение — никакого исполнения кода/SQL на проде. Org-изоляция как
у соответствующих таблиц; платформенный контекст (клейм pl без org) —
вся установка. Чтение журналов не журналируется (решение О6).
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from src.core.models import AuditEvent, EventOutbox, RecordVersion

LOG_SOURCES = ("audit", "egress", "outbox", "sync", "flow", "webhooks")


class DebuggerError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _c(text_value: str) -> str | None:
    return text_value or None


# ---------- §8.1 Журналы ----------

def fetch_logs(db: Session, *, source: str, org_id: uuid.UUID | None,
               filters: dict, limit: int, offset: int) -> tuple[list[dict], int]:
    """Один источник (merge — на клиенте). Возвращает (rows, total)."""
    rows_fn = {
        "audit": _logs_audit, "egress": _logs_egress, "outbox": _logs_outbox,
        "sync": _logs_sync, "flow": _logs_flow, "webhooks": _logs_webhooks,
    }.get(source)
    if rows_fn is None:
        raise DebuggerError("unknown_source",
                            f"source: {'|'.join(LOG_SOURCES)}")
    return rows_fn(db, org_id=org_id, filters=filters,
                   limit=limit, offset=offset)


def _logs_audit(db: Session, *, org_id, filters, limit, offset):
    query = select(AuditEvent)
    if org_id is not None:
        query = query.where(AuditEvent.company_id == org_id)
    action = _c(str(filters.get("action") or ""))
    if action:
        query = query.where(AuditEvent.action.like(f"{action}%"))
    user_id = _c(str(filters.get("user_id") or ""))
    if user_id:
        query = query.where(AuditEvent.user_id == uuid.UUID(user_id))
    entity_type = _c(str(filters.get("entity_type") or ""))
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    q = _c(str(filters.get("q") or ""))
    if q:
        query = query.where(AuditEvent.payload.cast(
            text("TEXT")).ilike(f"%{q}%"))
    return _paginate_audit(db, query, limit, offset)


def _logs_egress(db: Session, *, org_id, filters, limit, offset):
    """egress.request — журнал исходящих; записи без company_id →
    только платформенный контекст (§8.1)."""
    query = select(AuditEvent).where(AuditEvent.action == "egress.request")
    if org_id is not None:
        query = query.where(AuditEvent.company_id == org_id)
    else:
        host = _c(str(filters.get("host") or ""))
        if host:
            query = query.where(AuditEvent.payload["host"].as_string()
                                == host)
        status = _c(str(filters.get("status") or ""))
        if status:
            query = query.where(AuditEvent.payload["status"].as_string()
                                == status)
    return _paginate_audit(db, query, limit, offset)


def _paginate_audit(db: Session, query, limit, offset):
    total = db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery())) or 0
    rows = db.scalars(query.order_by(
        AuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    return ([{
        "at": row.created_at.isoformat(), "action": row.action,
        "user_id": str(row.user_id) if row.user_id else None,
        "entity_type": row.entity_type, "entity_id": row.entity_id,
        "payload": _safe_payload(row.payload),
    } for row in rows], int(total))


def _safe_payload(payload: dict | None) -> dict:
    """Payload как записан (журналы пишутся без секретов по коду);
    на всякий случай вырезаем известные секретные ключи."""
    if not isinstance(payload, dict):
        return {}
    banned = ("password", "token", "secret", "api_key", "credentials")
    return {k: v for k, v in payload.items()
            if not any(b in k.lower() for b in banned)}


def _logs_outbox(db: Session, *, org_id, filters, limit, offset):
    query = select(EventOutbox)
    if org_id is not None:
        query = query.where(
            EventOutbox.payload["company_id"].as_string() == str(org_id))
    event_name = _c(str(filters.get("event_name") or ""))
    if event_name:
        query = query.where(EventOutbox.event_name == event_name)
    if "processed" in filters and filters["processed"] is not None:
        query = query.where(EventOutbox.processed == bool(filters["processed"]))
    total = db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery())) or 0
    rows = db.scalars(query.order_by(
        EventOutbox.id.desc()).limit(limit).offset(offset)).all()
    return ([{
        "at": row.created_at.isoformat() if row.created_at else None,
        "id": row.id, "event_name": row.event_name,
        "processed": row.processed, "payload": _safe_payload(row.payload),
    } for row in rows], int(total))


def _logs_sync(db: Session, *, org_id, filters, limit, offset):
    from src.modules.integrations import models as im

    query = select(im.SyncRun, im.SyncJob.name).join(
        im.SyncJob, im.SyncJob.id == im.SyncRun.sync_job_id)
    if org_id is not None:
        query = query.where(im.SyncJob.company_id == org_id)
    status = _c(str(filters.get("status") or ""))
    if status:
        query = query.where(im.SyncRun.status == status)
    job_id = _c(str(filters.get("job_id") or ""))
    if job_id:
        query = query.where(im.SyncRun.sync_job_id == uuid.UUID(job_id))
    total = db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery())) or 0
    rows = db.execute(query.order_by(
        im.SyncRun.id.desc()).limit(limit).offset(offset)).all()
    return ([{
        "at": run.started_at.isoformat() if run.started_at else None,
        "id": run.id, "job": name, "status": run.status,
        "items_in": run.items_in, "items_out": run.items_out,
        "error": (run.error or "")[:300],
        "payload": _safe_payload(run.payload),
    } for run, name in rows], int(total))


def _logs_flow(db: Session, *, org_id, filters, limit, offset):
    from src.modules.integrations import models as im

    query = select(im.FlowRun, im.OnlinePayment.provider_payment_id).join(
        im.OnlinePayment, im.OnlinePayment.id == im.FlowRun.payment_id)
    if org_id is not None:
        query = query.where(im.OnlinePayment.company_id == org_id)
    status = _c(str(filters.get("status") or ""))
    if status:
        query = query.where(im.FlowRun.status == status)
    step = _c(str(filters.get("step") or ""))
    if step:
        query = query.where(im.FlowRun.step == step)
    error = _c(str(filters.get("error") or ""))
    if error:
        query = query.where(im.FlowRun.error.ilike(f"%{error}%"))
    total = db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery())) or 0
    rows = db.execute(query.order_by(
        im.FlowRun.id.desc()).limit(limit).offset(offset)).all()
    return ([{
        "at": None, "id": run.id,
        "payment": payment_ref, "status": run.status, "step": run.step,
        "attempts": run.attempts, "error": (run.error or "")[:300],
    } for run, payment_ref in rows], int(total))


def _logs_webhooks(db: Session, *, org_id, filters, limit, offset):
    from src.modules.integrations import models as im

    query = select(im.WebhookEvent, im.WebhookEndpoint.name).join(
        im.WebhookEndpoint, im.WebhookEndpoint.id == im.WebhookEvent.endpoint_id)
    if org_id is not None:
        query = query.where(im.WebhookEndpoint.company_id == org_id)
    status = _c(str(filters.get("status") or ""))
    if status:
        query = query.where(im.WebhookEvent.status == status)
    event_type = _c(str(filters.get("event_type") or ""))
    if event_type:
        query = query.where(im.WebhookEvent.event_type == event_type)
    total = db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery())) or 0
    rows = db.execute(query.order_by(
        im.WebhookEvent.id.desc()).limit(limit).offset(offset)).all()
    return ([{
        "at": None, "id": str(event.id), "endpoint": name,
        "status": event.status, "event_type": event.event_type,
        "error": (event.error or "")[:300],
    } for event, name in rows], int(total))


# ---------- §8.2 Трассировка ----------

def trace_entity(db: Session, *, entity_type: str, entity_id: str,
                 org_id: uuid.UUID | None, limit: int = 200) -> dict:
    """Таймлайн жизни сущности: версии + аудит + события шины (хронология)."""
    eid = str(entity_id)
    timeline: list[dict] = []

    versions = db.scalars(select(RecordVersion).where(
        RecordVersion.entity_type == entity_type,
        RecordVersion.entity_id == eid,
    ).order_by(RecordVersion.changed_at.desc()).limit(limit)).all()
    for version in versions:
        timeline.append({
            "at": version.changed_at.isoformat(),
            "source": "version",
            "changed_by": str(version.changed_by) if version.changed_by else None,
            "diff": version.diff, "reason": version.reason,
        })

    audit = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == entity_type,
        AuditEvent.entity_id == eid,
    ).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    for row in audit:
        if org_id is not None and row.company_id is not None \
                and str(row.company_id) != str(org_id):
            continue  # org-изоляция
        timeline.append({
            "at": row.created_at.isoformat(), "source": "audit",
            "action": row.action,
            "user_id": str(row.user_id) if row.user_id else None,
            "payload": _safe_payload(row.payload),
        })

    outbox = db.scalars(select(EventOutbox).where(or_(
        EventOutbox.payload["entity_id"].as_string() == eid,
        EventOutbox.payload["id"].as_string() == eid,
    )).order_by(EventOutbox.id.desc()).limit(limit)).all()
    for row in outbox:
        if org_id is not None and row.payload.get("company_id") is not None \
                and str(row.payload["company_id"]) != str(org_id):
            continue
        timeline.append({
            "at": row.created_at.isoformat() if row.created_at else None,
            "source": "outbox", "event_name": row.event_name,
            "processed": row.processed,
        })

    timeline.sort(key=lambda item: item["at"] or "", reverse=True)
    return {"entity_type": entity_type, "entity_id": eid,
            "timeline": timeline[:limit]}


# ---------- §8.3 Диагностика ----------

def diagnostics(db: Session, *, org_id: uuid.UUID | None) -> dict:
    """Агрегат здоровья (только чтение)."""
    from src import __version__
    from src.core.models import ModuleRegistry
    from src.core.plugins import MANIFESTS
    from src.modules.integrations import models as im

    t0 = time.monotonic()
    db.scalar(select(1))
    db_latency_ms = round((time.monotonic() - t0) * 1000, 1)

    redis_ok = False
    try:
        import redis as _redis

        from src.config import get_settings

        client = _redis.Redis.from_url(get_settings().redis_url,
                                       socket_timeout=2)
        redis_ok = bool(client.ping())
    except Exception:  # noqa: BLE001 — диагностика не должна падать
        redis_ok = False

    outbox_pending = db.scalar(select(func.count()).where(
        EventOutbox.processed.is_(False))) or 0
    outbox_oldest = db.scalar(select(func.min(EventOutbox.created_at).label("c")).where(
        EventOutbox.processed.is_(False)))

    failed_syncs = db.execute(select(
        im.SyncRun, im.SyncJob.name).join(
        im.SyncJob, im.SyncJob.id == im.SyncRun.sync_job_id).where(
        im.SyncRun.status.in_(("error", "failed"))).order_by(
        im.SyncRun.id.desc()).limit(10)).all()
    failed_flows = db.scalars(select(im.FlowRun).where(
        im.FlowRun.status.in_(("failed", "manual"))).order_by(
        im.FlowRun.id.desc()).limit(10)).all()

    connections_query = select(im.Connection)
    if org_id is not None:
        connections_query = connections_query.where(
            im.Connection.company_id == org_id)
    connections = db.scalars(connections_query.order_by(
        im.Connection.name)).all()

    modules = db.scalars(select(ModuleRegistry).order_by(
        ModuleRegistry.name)).all()

    return {
        "version": __version__,
        "modules": [{"name": m.name, "version": m.version,
                     "db_schema": m.db_schema, "is_active": m.is_active}
                    for m in modules],
        "manifests": [m.name for m in MANIFESTS],
        "health": {
            "db": {"ok": True, "latency_ms": db_latency_ms},
            "redis": {"ok": redis_ok},
            "outbox": {"pending": int(outbox_pending),
                       "oldest_at": outbox_oldest.isoformat()
                       if outbox_oldest else None},
        },
        "recent_failures": {
            "sync_runs": [{"job": name, "status": run.status,
                           "error": (run.error or "")[:200]}
                          for run, name in failed_syncs],
            "flow_runs": [{"status": run.status, "step": run.step,
                           "attempts": run.attempts,
                           "error": (run.error or "")[:200]}
                          for run in failed_flows],
        },
        "connections": [{"id": str(c.id), "name": c.name,
                         "connector_code": c.connector_code,
                         "is_active": c.is_active,
                         "last_check_at": c.last_check_at.isoformat()
                         if c.last_check_at else None,
                         "last_check_ok": c.last_check_ok}
                        for c in connections],
    }
