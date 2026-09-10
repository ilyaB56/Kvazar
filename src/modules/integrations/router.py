"""API интеграционной платформы: /api/v1/integrations/..."""

from __future__ import annotations

import secrets
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core import events
from src.core.auth import require_module
from src.core.models import User
from src.db import get_db
from src.modules.integrations import models as m
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict, encrypt_dict

router = APIRouter(tags=["integrations"])


# ---------- Schemas ----------

class ConnectionIn(BaseModel):
    name: str
    connector_code: str
    credentials: dict = {}
    config: dict = {}


class ConnectionOut(BaseModel):
    id: uuid.UUID
    name: str
    connector_code: str
    config: dict
    is_active: bool
    last_check_ok: bool | None

    model_config = {"from_attributes": True}


class WebhookIn(BaseModel):
    name: str
    target_module: str = "external"
    # провайдерский режим: коннектор авторизует вебхук (§4.1)
    connection_id: uuid.UUID | None = None


class WebhookOut(BaseModel):
    id: uuid.UUID
    name: str
    target_module: str
    url_path: str
    connection_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class MappingIn(BaseModel):
    name: str
    source_fields: list[str] = []
    target_fields: list[str] = []
    transformations: dict = {}


class SyncJobIn(BaseModel):
    name: str
    connection_id: uuid.UUID
    direction: str = "fetch"
    cron: str = ""
    endpoint: str = ""
    mapping_id: uuid.UUID | None = None
    emit_event: str = "integration.data.fetched"


class SyncJobPatch(BaseModel):
    is_active: bool | None = None
    cron: str | None = None
    endpoint: str | None = None


class RecipeIn(BaseModel):
    name: str
    definition: dict = {}
    is_published: bool = False


# ---------- Каталог коннекторов (магазин, backend) ----------

@router.get("/connectors")
def list_connectors(user: User = Depends(require_module("integrations", "ro"))):
    return connector_registry.available()


# ---------- Connections ----------

@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db)):
    return db.scalars(select(m.Connection)).all()


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionIn, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    if not any(c["code"] == body.connector_code for c in connector_registry.available()):
        raise HTTPException(400, f"Unknown connector: {body.connector_code}")
    connection = m.Connection(
        name=body.name,
        connector_code=body.connector_code,
        credentials_enc=encrypt_dict(body.credentials),
        config=body.config,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: uuid.UUID, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    connection = db.get(m.Connection, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found")
    connector = connector_registry.build(
        connection.connector_code, connection.config, decrypt_dict(connection.credentials_enc)
    )
    result = connector.test_connection()
    from datetime import UTC, datetime

    connection.last_check_at = datetime.now(UTC)
    connection.last_check_ok = result.ok
    db.commit()
    return {"ok": result.ok, "error": result.error}


# ---------- Webhooks (входящие) ----------

@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db)):
    endpoints = db.scalars(select(m.WebhookEndpoint)).all()
    return [
        WebhookOut(
            id=e.id, name=e.name, target_module=e.target_module,
            url_path=f"/api/v1/integrations/hooks/{e.id}",
        )
        for e in endpoints
    ]


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    if body.connection_id is not None:
        connection = db.get(m.Connection, body.connection_id)
        if connection is None:
            raise HTTPException(422, "Unknown connection")
    endpoint = m.WebhookEndpoint(
        name=body.name,
        secret_token=secrets.token_urlsafe(32),
        target_module=body.target_module,
        connection_id=body.connection_id,
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return {
        "id": endpoint.id,
        "url_path": f"/api/v1/integrations/hooks/{endpoint.id}",
        "secret_token": endpoint.secret_token,  # показываем один раз
    }


def _webhook_event_type(payload: dict) -> str:
    """Тип события нотификации: payment.succeeded / cancellation.succeeded …
    (единая схема объект.тип; generic-приёмники могут нести своё поле)."""
    return str(payload.get("event") or payload.get("type") or "unknown")


def _record_webhook_event(
    db: Session, *, endpoint: m.WebhookEndpoint, connection_id: uuid.UUID | None,
    external_key: str, event_type: str, payload: dict, status: str, error: str = "",
) -> m.WebhookEvent | None:
    """Журнал + идемпотентность (§4.2): дубль по (connection, external_key)
    → статус duplicate у существующей записи, новая не создаётся."""
    from datetime import UTC, datetime

    existing = db.scalar(select(m.WebhookEvent).where(
        m.WebhookEvent.connection_id == connection_id
        if connection_id else m.WebhookEvent.connection_id.is_(None),
        m.WebhookEvent.external_key == external_key,
    ))
    if existing is not None:
        if existing.status != "duplicate":
            existing.status = "duplicate"
            db.commit()
        return None
    row = m.WebhookEvent(
        endpoint_id=endpoint.id,
        connection_id=connection_id,
        external_key=external_key,
        event_type=event_type,
        payload=payload,
        status=status,
        error=error,
    )
    if status == "processed":
        row.processed_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    return row


@router.post("/hooks/{endpoint_id}", status_code=202)
async def receive_hook(
    endpoint_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    x_erp_token: str | None = Header(default=None),
):
    """Публичный приёмник webhook'ов (sales-automation §3.2, ADR-002).

    Два режима (§4.1):
    - endpoint с connection_id: авторизация коннектором провайдера —
      verify_webhook (подпись) и/или verify_by_fetch (повторный запрос
      статуса платежа; ЮKassa нотификации не подписывает); X-ERP-Token
      не требуется; дубли гасятся журналом webhook_events;
    - без connection_id: прежний режим X-ERP-Token (generic).
    """
    endpoint = db.get(m.WebhookEndpoint, endpoint_id)
    if endpoint is None or not endpoint.is_active:
        raise HTTPException(404, "Unknown endpoint")

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid JSON body")
    event_type = _webhook_event_type(payload)

    if endpoint.connection_id is None:
        # generic-режим: токен обязателен, журнал тоже ведём
        if not x_erp_token or not secrets.compare_digest(x_erp_token, endpoint.secret_token):
            raise HTTPException(401, "Invalid token")
        row = _record_webhook_event(
            db, endpoint=endpoint, connection_id=None,
            external_key=f"token:{hashlib.sha256(raw_body).hexdigest()[:40]}",
            event_type=event_type, payload=payload, status="new",
        )
        if row is None:
            return {"accepted": True, "duplicate": True}
        events.publish(db, f"webhook.received.{endpoint.target_module}", payload)
        row.status = "processed"
        db.commit()
        events.dispatch_outbox(db)
        return {"accepted": True}

    # режим провайдера: коннектор решает, верить ли телу (§3.4)
    connection = db.get(m.Connection, endpoint.connection_id)
    if connection is None or not connection.is_active:
        raise HTTPException(409, "Endpoint connection is inactive")
    from .crypto import decrypt_dict
    connector = connector_registry.build(
        connection.connector_code, connection.config, decrypt_dict(connection.credentials_enc),
    )
    payment_id = ""
    if hasattr(connector, "payment_id"):
        payment_id = connector.payment_id(payload)
    external_key = (
        connector.external_key(event_type, payload)
        if hasattr(connector, "external_key") else f"{payment_id}:{event_type}"
    )
    if not external_key or external_key.strip(" :") == "":
        raise HTTPException(422, "Payload has no payment id")

    # проверка подлинности: подпись (если провайдер подписывает) и/или
    # повторный запрос статуса (verify_by_fetch обязателен для неподписанных)
    headers = {k.lower(): v for k, v in request.headers.items()}
    signed = connector.verify_webhook(headers, raw_body)
    fetched = connector.verify_by_fetch(payment_id) if payment_id else None
    verified = signed or (fetched is not None and fetched.ok)
    if not verified:
        error = (fetched.error if fetched else "") or "verification_failed"
        _record_webhook_event(
            db, endpoint=endpoint, connection_id=connection.id,
            external_key=external_key, event_type=event_type,
            payload=payload, status="invalid", error=error,
        )
        raise HTTPException(401, f"Webhook verification failed: {error}")

    row = _record_webhook_event(
        db, endpoint=endpoint, connection_id=connection.id,
        external_key=external_key, event_type=event_type,
        payload=payload, status="new",
    )
    if row is None:
        return {"accepted": True, "duplicate": True}
    events.publish(db, "integration.webhook.verified", {
        "webhook_event_id": str(row.id),
        "connection_id": str(connection.id),
        "event": event_type,
        "payment_id": payment_id,
    })
    row.status = "processed"
    db.commit()
    events.dispatch_outbox(db)
    return {"accepted": True, "webhook_event_id": str(row.id)}


# ---------- Mappings ----------

@router.post("/mappings", status_code=201)
def create_mapping(body: MappingIn, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    mapping = m.FieldMapping(**body.model_dump())
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


# ---------- Sync jobs ----------

@router.get("/sync-jobs")
def list_sync_jobs(user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db)):
    return db.scalars(select(m.SyncJob).order_by(m.SyncJob.created_at)).all()


@router.post("/sync-jobs", status_code=201)
def create_sync_job(body: SyncJobIn, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    if db.get(m.Connection, body.connection_id) is None:
        raise HTTPException(400, "Unknown connection")
    job = m.SyncJob(**body.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/sync-jobs/{job_id}/run")
def run_sync_job(job_id: uuid.UUID, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    from src.modules.integrations.tasks import run_job

    run_job.delay(str(job_id))
    return {"queued": True}


@router.patch("/sync-jobs/{job_id}")
def patch_sync_job(job_id: uuid.UUID, body: SyncJobPatch, user: User = Depends(require_module("integrations")),
                   db: Session = Depends(get_db)):
    """Правка задания (вкл/выкл, cron, endpoint) — планировщик подхватит сам."""
    job = db.get(m.SyncJob, job_id)
    if job is None:
        raise HTTPException(404, "Sync job not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(job, field, value)
    db.commit()
    db.refresh(job)
    return job


@router.get("/sync-runs")
def list_runs(sync_job_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db)):
    runs = db.scalars(
        select(m.SyncRun)
        .where(m.SyncRun.sync_job_id == sync_job_id)
        .order_by(m.SyncRun.id.desc())
        .limit(50)
    ).all()
    return runs


# ---------- Recipes (no-code конструктор) ----------

@router.post("/recipes", status_code=201)
def create_recipe(body: RecipeIn, user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    recipe = m.Recipe(**body.model_dump())
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


# ---------- Правила уведомлений (showcase-chain, этап D) ----------

class NotificationRuleIn(BaseModel):
    name: str
    event_name: str
    chat_id: str
    template: str = ""
    is_active: bool = True


class NotificationRuleOut(BaseModel):
    id: uuid.UUID
    name: str
    event_name: str
    chat_id: str
    template: str
    is_active: bool

    model_config = {"from_attributes": True}


@router.get("/notification-rules", response_model=list[NotificationRuleOut])
def list_notification_rules(user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    return db.scalars(select(m.NotificationRule).order_by(m.NotificationRule.created_at)).all()


@router.post("/notification-rules", response_model=NotificationRuleOut, status_code=201)
def create_notification_rule(body: NotificationRuleIn, user: User = Depends(require_module("integrations")),
                             db: Session = Depends(get_db)):
    from src.modules.integrations.notify import NOTIFY_EVENTS

    if body.event_name not in NOTIFY_EVENTS:
        raise HTTPException(422, f"event must be one of {list(NOTIFY_EVENTS)}")
    rule = m.NotificationRule(**body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/notification-rules/{rule_id}", response_model=NotificationRuleOut)
def delete_notification_rule(rule_id: uuid.UUID, user: User = Depends(require_module("integrations")),
                             db: Session = Depends(get_db)):
    rule = db.get(m.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Notification rule not found")
    db.delete(rule)
    db.commit()
    return rule


@router.post("/notification-rules/{rule_id}/test")
def test_notification_rule(rule_id: uuid.UUID, user: User = Depends(require_module("integrations")),
                           db: Session = Depends(get_db)):
    """«Тест»: отправить Hello по правилу (рендер шаблона на дефолт-полях)."""
    from src.modules.integrations.notify import send_notification

    rule = db.get(m.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Notification rule not found")
    payload = {"event": rule.event_name, "doc_number": "TEST-1", "amount": "0",
               "job": "test", "error": "тестовая отправка", "version": "0.1.0"}
    ok = send_notification(rule, payload)
    return {"ok": ok}


@router.get("/recipes")
def list_recipes(user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db), published_only: bool = False):
    query = select(m.Recipe)
    if published_only:
        query = query.where(m.Recipe.is_published.is_(True))
    return db.scalars(query).all()
