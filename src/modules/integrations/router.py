"""API интеграционной платформы: /api/v1/integrations/..."""

from __future__ import annotations

import secrets
import hashlib
import json
import uuid

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.core import events
from src.core.auth import module_level, require_module
from src.core.auth import CompanyScoped
from src.core.models import User
from src.db import get_db
from src.modules.integrations import models as m
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict, encrypt_dict

from src.core.pagination import Page, PageParams, page_params
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
    model_config = {"json_schema_extra": {"example": {
        "name": "Онлайн-продажа цифровых",
        "definition": {
            "trigger_event": "integration.payment.received",
            "action": {
                "type": "sales_flow",
                # connection_id = подключение провайдера (эквайринг):
                # по нему выбирается рецепт вебхука этого провайдера
                "connection_id": "uuid подключения yookassa",
                "config": {
                    "account_id": "uuid счёта зачисления",
                    "price_tolerance": "0",
                    "on_no_items": "transaction_only",
                    "delivery_channel": "email",
                    "smtp_connection_id": "uuid smtp-подключения",
                },
            },
            # служебное подключение http_rest → наш API (токен роли user)
            "api_connection_id": "uuid служебного подключения",
        },
    }}}

    name: str
    definition: dict = {}
    is_published: bool = False


# ---------- Каталог коннекторов (магазин, backend) ----------

@router.get("/connectors")
def list_connectors(user: User = Depends(require_module("integrations", "ro"))):
    return connector_registry.available()


# ---------- Connections ----------

@router.get("/connections", response_model=list[ConnectionOut] | Page[ConnectionOut])
def list_connections(user: User = Depends(require_module("integrations", "ro")),
                    db: Session = Depends(get_db), scoped: CompanyScoped = None,
                    page: PageParams = Depends(page_params)):
    query = select(m.Connection).where(m.Connection.company_id == scoped)
    if getattr(user, "token_pl", False):
        # платформенный админ видит и платформенные коннекторы (NULL)
        query = select(m.Connection).where(or_(
            m.Connection.company_id == scoped, m.Connection.company_id.is_(None)))
    return page.apply(db, query)


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionIn, user: User = Depends(require_module("integrations")),
                     db: Session = Depends(get_db), scoped: CompanyScoped = None):
    if not any(c["code"] == body.connector_code for c in connector_registry.available()):
        raise HTTPException(400, f"Unknown connector: {body.connector_code}")
    connection = m.Connection(
        company_id=scoped,
        name=body.name,
        connector_code=body.connector_code,
        credentials_enc=encrypt_dict(body.credentials),
        config=body.config,
    )
    db.add(connection)
    db.flush()
    # маркетплейсы: задания синхронизации по умолчанию — товары/час,
    # заказы/15мин, транзакции/час (спеки §3); идемпотентно
    if body.connector_code == "ozon_seller":
        from src.modules.integrations.ozon import seed_sync_jobs

        seed_sync_jobs(db, connection)
    if body.connector_code == "wb_seller":
        from src.modules.integrations.wb import seed_sync_jobs

        seed_sync_jobs(db, connection)
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


class ConnectionPatch(BaseModel):
    """Деактивация/активация и переименование. Удаления нет сознательно:
    на connection ссылаются платежи/задания/события — история сохраняется,
    «выключение» — это is_active=false."""
    model_config = {"json_schema_extra": {"example": {
        "name": "ЮKassa (прод)", "is_active": False}}}

    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
def patch_connection(connection_id: uuid.UUID, body: ConnectionPatch,
                     user: User = Depends(require_module("integrations")),
                     db: Session = Depends(get_db)):
    """Изменить is_active (деактивация вместо удаления) и/или name."""
    connection = db.get(m.Connection, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found")
    if body.name is not None:
        connection.name = body.name
    if body.is_active is not None:
        connection.is_active = body.is_active
    from src.core.models import AuditEvent

    db.add(AuditEvent(
        action="connection.updated", entity_type="connection",
        entity_id=str(connection.id),
        payload={"name": body.name, "is_active": body.is_active}))
    db.commit()
    db.refresh(connection)
    return connection


# ---------- Webhooks (входящие) ----------

@router.get("/webhooks", response_model=list[WebhookOut] | Page[WebhookOut])
def list_webhooks(user: User = Depends(require_module("integrations", "ro")),
                  db: Session = Depends(get_db), scoped: CompanyScoped = None,
                  page: PageParams = Depends(page_params)):
    return page.apply(
        db,
        select(m.WebhookEndpoint).where(m.WebhookEndpoint.company_id == scoped),
        transform=lambda e: WebhookOut(
            id=e.id, name=e.name, target_module=e.target_module,
            url_path=f"/api/v1/integrations/hooks/{e.id}",
        ),
    )


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, user: User = Depends(require_module("integrations")),
                   db: Session = Depends(get_db), scoped: CompanyScoped = None):
    if body.connection_id is not None:
        connection = db.get(m.Connection, body.connection_id)
        if connection is None:
            raise HTTPException(422, "Unknown connection")
    endpoint = m.WebhookEndpoint(
        company_id=scoped,
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
        # фикс из реестра: invalid остаётся invalid (инцидент для разбора),
        # во duplicate помечаем только уже обработанные/ошибочные записи
        if existing.status in ("new", "processed", "error"):
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


def _flow_recipe_and_api(db: Session, connection) -> tuple:
    """Рецепт с trigger_event=integration.payment.received + клиент API
    учёта. Токен — credentials.api_key connection типа http_rest, чей
    base_url указывает на наш API (showcase-chain: служебная учётка)."""
    # рецепт этого провайдера: action.connection_id == connection.id;
    # рецептов с разных эквайринг-подключений может быть несколько
    candidates = db.scalars(select(m.Recipe).where(
        m.Recipe.is_published.is_(True),
        m.Recipe.definition["trigger_event"].as_string()
        == "integration.payment.received",
    )).all()
    recipe = next((r for r in candidates
                   if (r.definition or {}).get("action", {}).get("connection_id")
                   == str(connection.id)), None)
    if recipe is None:
        recipe = next((r for r in candidates
                       if not (r.definition or {}).get("action", {}).get("connection_id")),
                      None)
    if recipe is None:
        return None, None
    definition = recipe.definition or {}
    # api_connection_id — служебный http_rest-коннектор с X-API-Token;
    # action.connection_id — провайдер платежа (не для вызовов API!)
    token_conn_id = definition.get("api_connection_id")
    if token_conn_id:
        token_conn = db.get(m.Connection, uuid.UUID(str(token_conn_id)))
    else:
        token_conn = db.scalar(select(m.Connection).where(
            m.Connection.connector_code == "http_rest", m.Connection.is_active.is_(True)))
    if token_conn is None:
        return recipe, None
    from .crypto import decrypt_dict
    from .sales_flow import AccountingApi

    creds = decrypt_dict(token_conn.credentials_enc)
    config = token_conn.config or {}
    # base с /api/v1 или без — нормализует AccountingApi
    api = AccountingApi(config.get("base_url", "http://api:8000"),
                        creds.get("api_key", ""))
    return recipe, api


def get_db_session():
    """Сессия вне Depends (threadpool-обработка вебхука)."""
    from src.db import SessionLocal

    return SessionLocal()


def _process_hook(endpoint_id: uuid.UUID, raw_body: bytes, headers: dict,
                  x_erp_token: str | None) -> dict:
    """Синхронная обработка вебхука (выполняется в threadpool — внутри
    httpx-вызовы в собственный API, из async-контекста это дедлок)."""
    db = get_db_session()
    try:
        return _process_hook_db(db, endpoint_id, raw_body, headers, x_erp_token)
    finally:
        db.close()


def _process_hook_db(db: Session, endpoint_id: uuid.UUID, raw_body: bytes,
                     headers: dict, x_erp_token: str | None) -> dict:
    endpoint = db.get(m.WebhookEndpoint, endpoint_id)
    if endpoint is None or not endpoint.is_active:
        raise HTTPException(404, "Unknown endpoint")

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
    # этап B: нотификация об оплате → нормализация + sales_flow
    payment_out = {"payment_id": None, "flow": None}
    if event_type.startswith("payment.") and hasattr(connector, "normalize"):
        from . import sales_flow as flow_mod
        payment = flow_mod.normalize_payment(db, connection=connection,
                                             connector=connector, event=row)
        if payment is not None:
            payment_out["payment_id"] = str(payment.id)
            recipe, api = _flow_recipe_and_api(db, connection)
            if recipe is not None and api is not None:
                run = flow_mod.run_sales_flow(db, payment=payment, recipe=recipe, api=api)
                payment_out["flow"] = {"status": run.status, "step": run.step,
                                       "error": run.error[:200]}
    return {"accepted": True, "webhook_event_id": str(row.id), **payment_out}


@router.post("/hooks/{endpoint_id}", status_code=202)
async def receive_hook(
    endpoint_id: uuid.UUID,
    request: Request,
    x_erp_token: str | None = Header(default=None),
):
    """Публичный приёмник webhook'ов (sales-automation §3.2, ADR-002).

    Два режима (§4.1): endpoint с connection_id — авторизация коннектором
    провайдера (verify_webhook/verify_by_fetch), без — X-ERP-Token.
    Обработка идёт в threadpool: внутри httpx-вызовы в собственный API.
    """
    import asyncio

    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    return await asyncio.to_thread(
        _process_hook, endpoint_id, raw_body, headers, x_erp_token)



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
def list_sync_jobs(user: User = Depends(require_module("integrations", "ro")),
                  db: Session = Depends(get_db), scoped: CompanyScoped = None,
                  page: PageParams = Depends(page_params)):
    return page.apply(db, select(m.SyncJob).where(
        m.SyncJob.company_id == scoped).order_by(m.SyncJob.created_at))


@router.post("/sync-jobs", status_code=201)
def create_sync_job(body: SyncJobIn, user: User = Depends(require_module("integrations")),
                     db: Session = Depends(get_db), scoped: CompanyScoped = None):
    if db.get(m.Connection, body.connection_id) is None:
        raise HTTPException(400, "Unknown connection")
    job = m.SyncJob(**body.model_dump(), company_id=scoped)
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
def list_runs(sync_job_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
              db: Session = Depends(get_db), scoped: CompanyScoped = None,
              page: PageParams = Depends(page_params)):
    job = db.get(m.SyncJob, sync_job_id)
    if job is None or job.company_id != scoped:
        raise HTTPException(404, "Sync job not found")
    return page.apply(db, select(m.SyncRun)
                      .where(m.SyncRun.sync_job_id == sync_job_id)
                      .order_by(m.SyncRun.id.desc()))



# ---------- Онлайн-платежи (sales-automation §5.1, этап B) ----------

class PaymentOut(BaseModel):
    model_config = {
        "from_attributes": True,
        # пример GET /integrations/payments (buyer показан как видит rw;
        # ro получает замаскированного «b***@e***.com»)
        "json_schema_extra": {"example": {
            "id": "0bd6d4a6-...-f2c1", "connection_id": "9af1c2b0-...",
            "provider": "yookassa", "provider_payment_id": "2b5d9f30-000f-5000-9000-1e2a7b6d4c3a",
            "status": "processed", "amount": "1000.00", "currency": "RUB",
            "buyer": {"email": "buyer@example.com"},
            "lines": [{"external_id": "site-sku-1", "sku": "DIGI-1", "qty": 2,
                       "unit_price": "500.00"}],
            "sales_order_id": "…", "transaction_id": "…", "shipment_id": "…",
            "error_step": "", "error_reason": "",
        }},
    }

    id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    provider_payment_id: str
    status: str
    amount: str
    currency: str
    # ПДн: без integrations rw buyer маскируется (§4.3)
    buyer: dict = {}
    lines: list = []
    sales_order_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    shipment_id: uuid.UUID | None = None
    error_step: str = ""
    error_reason: str = ""
    created_at: object = None


def _mask_buyer(buyer: dict) -> dict:
    def mask(value: str) -> str:
        if "@" in value:
            head, _, tail = value.partition("@")
            return f"{head[:2]}…@{tail}" if len(head) > 2 else "…@" + tail
        return value[:3] + "…" if len(value) > 3 else "…"
    return {k: mask(str(v)) if v else "" for k, v in (buyer or {}).items()}


def _payment_out(payment: m.OnlinePayment, rw: bool) -> PaymentOut:
    # amount — Decimal в БД, API отдаёт строкой (ADR-003)
    out = PaymentOut(
        id=payment.id, connection_id=payment.connection_id,
        provider=payment.provider, provider_payment_id=payment.provider_payment_id,
        status=payment.status, amount=str(payment.amount), currency=payment.currency,
        buyer=_mask_buyer(payment.buyer or {}) if not rw else dict(payment.buyer or {}),
        lines=list(payment.lines or []),
        sales_order_id=payment.sales_order_id, transaction_id=payment.transaction_id,
        shipment_id=payment.shipment_id, error_step=payment.error_step,
        error_reason=payment.error_reason, created_at=payment.created_at,
    )
    return out


# ---------- Ozon Seller: списки, sync, push, маржа (§5) ----------

def _ozon_scoped(db, user, connection_id: uuid.UUID):
    scoped = getattr(user, "token_org", None) or user.company_id
    connection = db.get(m.Connection, connection_id)
    if connection is None or connection.connector_code != "ozon_seller" \
            or (scoped is not None
                and connection.company_id != uuid.UUID(str(scoped))):
        raise HTTPException(404, "Ozon connection not found")
    return connection


@router.get("/ozon/products")
def ozon_products(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                  db: Session = Depends(get_db)):
    _ozon_scoped(db, user, connection_id)
    return db.scalars(select(m.OzonProduct).where(
        m.OzonProduct.connection_id == connection_id)
        .order_by(m.OzonProduct.offer_id)).all()


@router.get("/ozon/stocks")
def ozon_stocks(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                db: Session = Depends(get_db)):
    _ozon_scoped(db, user, connection_id)
    return db.scalars(select(m.OzonStock).where(
        m.OzonStock.connection_id == connection_id)).all()


@router.get("/ozon/orders")
def ozon_orders(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                db: Session = Depends(get_db)):
    _ozon_scoped(db, user, connection_id)
    return db.scalars(select(m.OzonOrder).where(
        m.OzonOrder.connection_id == connection_id)
        .order_by(m.OzonOrder.created_at.desc())).all()


@router.get("/ozon/transactions")
def ozon_transactions(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                      db: Session = Depends(get_db)):
    _ozon_scoped(db, user, connection_id)
    return db.scalars(select(m.OzonTransaction).where(
        m.OzonTransaction.connection_id == connection_id)
        .order_by(m.OzonTransaction.created_at.desc())).all()


class OzonSyncIn(BaseModel):
    connection_id: uuid.UUID
    kinds: list[str] = Field(default_factory=lambda: ["products"])


@router.post("/ozon/sync")
def ozon_sync(body: OzonSyncIn, user: User = Depends(require_module("integrations")),
              db: Session = Depends(get_db)):
    """Внеочередной прогон (синхронно): kinds — products|stocks|orders|
    transactions."""
    connection = _ozon_scoped(db, user, body.connection_id)
    connector = connector_registry.build(
        connection.connector_code, connection.config,
        decrypt_dict(connection.credentials_enc))
    from . import ozon as ozon_mod

    out = {}
    for kind in body.kinds:
        out[kind] = ozon_mod.run_ozon_sync(db, kind=kind,
                                           connection=connection,
                                           connector=connector)
        if not out[kind].get("ok"):
            raise HTTPException(409, f"{kind}: {out[kind].get('error')}")
    return out


@router.post("/ozon/push-stocks")
def ozon_push_stocks(body: OzonSyncIn, user: User = Depends(require_module("integrations")),
                     db: Session = Depends(get_db)):
    """Push наших остатков на Ozon (§3.3.5): смапленные items → суммарный
    остаток по складам → POST /v1/product/import/stocks."""
    connection = _ozon_scoped(db, user, body.connection_id)
    mappings = db.scalars(select(m.ItemMapping).where(
        m.ItemMapping.connection_id == connection.id,
        m.ItemMapping.is_active.is_(True))).all()
    offer_by_item = {mp.item_id: mp.external_item_id for mp in mappings}
    from src.modules.mgmt_accounting.features.inventory import service as inv_svc

    stocks = []
    for item_id, offer_id in offer_by_item.items():
        rows = inv_svc.stock_balances(db, item_id=item_id,
                                      company_id=connection.company_id)
        qty = sum(int(float(r["qty"])) for r in rows) if rows else 0
        stocks.append({"offer_id": offer_id, "product_id": 0, "stock": qty})
    connector = connector_registry.build(
        connection.connector_code, connection.config,
        decrypt_dict(connection.credentials_enc))
    result = connector.push(payload={"stocks": stocks})
    if not result.ok:
        raise HTTPException(409, result.error)
    return {"pushed": len(stocks)}


@router.get("/ozon/margin")
def ozon_margin(connection_id: uuid.UUID, date_from: str, date_to: str,
                include_cost: bool = False,
                user: User = Depends(require_module("integrations", "ro")),
                db: Session = Depends(get_db)):
    """«Ozon: комиссия и прибыль» (§5): выручка (delivered за период) −
    комиссии/логистика/реклама − себестоимость (avg_cost, флаг
    include_cost — решение ревью §10.3)."""
    connection = _ozon_scoped(db, user, connection_id)
    orders = db.scalars(select(m.OzonOrder).where(
        m.OzonOrder.connection_id == connection_id,
        m.OzonOrder.status == "delivered")).all()
    in_period = [o for o in orders
                 if date_from <= (o.order_date or "")[:10] <= date_to]
    revenue = sum((o.amount or Decimal("0") for o in in_period), Decimal("0"))
    from .ozon_docs import expense_category

    fees = logistics = advertising = Decimal("0")
    for t in db.scalars(select(m.OzonTransaction).where(
            m.OzonTransaction.connection_id == connection_id)).all():
        if not (date_from <= (t.posted_at or "")[:10] <= date_to):
            continue
        if str(t.operation_type).lower() == "transfer":
            continue  # выплаты — не комиссия (§10.2)
        name = expense_category(t.operation_type)
        amount = abs(t.amount or Decimal("0"))
        if name == "Логистика Ozon":
            logistics += amount
        elif name == "Реклама Ozon":
            advertising += amount
        else:
            fees += amount
    cost = Decimal("0")
    if include_cost:
        from src.modules.mgmt_accounting.features.inventory import models as inv_m

        avg_by_item = {row[0]: row[1] for row in db.execute(
            select(inv_m.Item.id, inv_m.Item.avg_cost).where(
                inv_m.Item.company_id == connection.company_id)).all()
            if row[1] is not None}
        item_by_offer = {mp.external_item_id: mp.item_id for mp in
                         db.scalars(select(m.ItemMapping).where(
                             m.ItemMapping.connection_id == connection_id)).all()}
        for o in in_period:
            for line in o.lines or []:
                item_id = item_by_offer.get(str(line.get("offer_id", "")))
                if item_id and item_id in avg_by_item:
                    cost += (avg_by_item[item_id] or Decimal("0")) * Decimal(str(line.get("qty", 1)))
    profit = revenue - fees - logistics - advertising - cost
    return {"date_from": date_from, "date_to": date_to,
            "orders_count": len(in_period),
            "revenue": str(revenue), "fees": str(fees),
            "logistics": str(logistics), "advertising": str(advertising),
            "cost": str(cost), "include_cost": include_cost,
            "profit": str(profit)}


# ---------- Wildberries: списки, sync, push, маржа (§5) ----------

def _wb_scoped(db, user, connection_id: uuid.UUID):
    scoped = getattr(user, "token_org", None) or user.company_id
    connection = db.get(m.Connection, connection_id)
    if connection is None or connection.connector_code != "wb_seller" \
            or (scoped is not None
                and connection.company_id != uuid.UUID(str(scoped))):
        raise HTTPException(404, "WB connection not found")
    return connection


@router.get("/wb/products")
def wb_products(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                db: Session = Depends(get_db)):
    _wb_scoped(db, user, connection_id)
    return db.scalars(select(m.WBProduct).where(
        m.WBProduct.connection_id == connection_id)
        .order_by(m.WBProduct.nm_id)).all()


@router.get("/wb/stocks")
def wb_stocks(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
              db: Session = Depends(get_db)):
    _wb_scoped(db, user, connection_id)
    return db.scalars(select(m.WBStock).where(
        m.WBStock.connection_id == connection_id)).all()


@router.get("/wb/orders")
def wb_orders(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
              db: Session = Depends(get_db)):
    _wb_scoped(db, user, connection_id)
    return db.scalars(select(m.WBOrder).where(
        m.WBOrder.connection_id == connection_id)
        .order_by(m.WBOrder.created_at.desc())).all()


@router.get("/wb/transactions")
def wb_transactions(connection_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                    db: Session = Depends(get_db)):
    _wb_scoped(db, user, connection_id)
    return db.scalars(select(m.WBTransaction).where(
        m.WBTransaction.connection_id == connection_id)
        .order_by(m.WBTransaction.created_at.desc())).all()


class WBSyncIn(BaseModel):
    connection_id: uuid.UUID
    kinds: list[str] = Field(default_factory=lambda: ["products"])


@router.post("/wb/sync")
def wb_sync(body: WBSyncIn, user: User = Depends(require_module("integrations")),
            db: Session = Depends(get_db)):
    """Внеочередной прогон (синхронно): kinds — products|stocks|orders|
    transactions."""
    connection = _wb_scoped(db, user, body.connection_id)
    connector = connector_registry.build(
        connection.connector_code, connection.config,
        decrypt_dict(connection.credentials_enc))
    from . import wb as wb_mod

    out = {}
    for kind in body.kinds:
        out[kind] = wb_mod.run_wb_sync(db, kind=kind, connection=connection,
                                       connector=connector)
        if not out[kind].get("ok"):
            raise HTTPException(409, f"{kind}: {out[kind].get('error')}")
    return out


class WBPushIn(BaseModel):
    connection_id: uuid.UUID
    warehouse_id: str = ""  # WB warehouseID; пусто — первый из config.warehouses


@router.post("/wb/push-stocks")
def wb_push_stocks(body: WBPushIn, user: User = Depends(require_module("integrations")),
                   db: Session = Depends(get_db)):
    """Push наших остатков на WB (§3.3.5): смапленные items (vendor_code) →
    остаток по ПРИВЯЗАННОЙ локации (config.warehouses: WB warehouseID →
    наш location_id) → PUT /api/v3/stocks/{warehouseId}."""
    import json as _json

    connection = _wb_scoped(db, user, body.connection_id)
    try:
        warehouses = _json.loads((connection.config or {}).get("warehouses") or "{}")
    except ValueError:
        warehouses = {}
    if not warehouses:
        raise HTTPException(422, "config.warehouses is empty (WB warehouseID → location_id)")
    warehouse_id = body.warehouse_id or next(iter(warehouses))
    location_id = warehouses.get(warehouse_id)
    if not location_id:
        raise HTTPException(422, f"warehouse {warehouse_id} not in config.warehouses")

    mappings = db.scalars(select(m.ItemMapping).where(
        m.ItemMapping.connection_id == connection.id,
        m.ItemMapping.is_active.is_(True))).all()
    # vendor_code → (наш item_id, nm_id из wb_products)
    nm_by_vendor = {row.vendor_code: row.nm_id for row in db.scalars(
        select(m.WBProduct).where(
            m.WBProduct.connection_id == connection.id)).all()
        if row.vendor_code}
    from src.modules.mgmt_accounting.features.inventory import service as inv_svc

    stocks = []
    for mp in mappings:
        rows = inv_svc.stock_balances(db, item_id=mp.item_id,
                                      location_id=uuid.UUID(str(location_id)),
                                      company_id=connection.company_id)
        qty = sum(int(float(r["qty"])) for r in rows) if rows else 0
        stocks.append({
            "nmId": int(nm_by_vendor.get(mp.external_item_id, 0) or 0),
            "vendorCode": mp.external_item_id,
            "stock": qty,
        })
    connector = connector_registry.build(
        connection.connector_code, connection.config,
        decrypt_dict(connection.credentials_enc))
    result = connector.push(payload={"warehouse_id": warehouse_id,
                                     "stocks": stocks})
    if not result.ok:
        raise HTTPException(409, result.error)
    return {"pushed": len(stocks), "warehouse_id": warehouse_id}


@router.get("/wb/margin")
def wb_margin(connection_id: uuid.UUID, date_from: str, date_to: str,
              include_cost: bool = False,
              user: User = Depends(require_module("integrations", "ro")),
              db: Session = Depends(get_db)):
    """«WB: комиссия и прибыль» (§5): выручка (delivered за период) −
    комиссии/логистика/хранение/штрафы/налог/прочее − себестоимость
    (avg_cost по маппингу vendor_code, флаг include_cost)."""
    connection = _wb_scoped(db, user, connection_id)
    orders = db.scalars(select(m.WBOrder).where(
        m.WBOrder.connection_id == connection_id,
        m.WBOrder.status == "delivered")).all()
    in_period = [o for o in orders
                 if date_from <= (o.order_date or "")[:10] <= date_to]
    revenue = sum((o.amount or Decimal("0") for o in in_period), Decimal("0"))
    from .wb_docs import WB_CATEGORIES, expense_category

    buckets = {name: Decimal("0") for name in WB_CATEGORIES.values()}
    for t in db.scalars(select(m.WBTransaction).where(
            m.WBTransaction.connection_id == connection_id)).all():
        if not (date_from <= (t.posted_at or "")[:10] <= date_to):
            continue
        if str(t.operation_type).lower() == "payment":
            continue  # выплаты — не комиссия (§10.2)
        buckets[expense_category(t.operation_type)] += abs(t.amount or Decimal("0"))
    cost = Decimal("0")
    if include_cost:
        from src.modules.mgmt_accounting.features.inventory import models as inv_m

        avg_by_item = {row[0]: row[1] for row in db.execute(
            select(inv_m.Item.id, inv_m.Item.avg_cost).where(
                inv_m.Item.company_id == connection.company_id)).all()
            if row[1] is not None}
        item_by_vendor = {mp.external_item_id: mp.item_id for mp in
                          db.scalars(select(m.ItemMapping).where(
                              m.ItemMapping.connection_id == connection_id)).all()}
        for o in in_period:
            for line in o.lines or []:
                item_id = item_by_vendor.get(str(line.get("vendor_code", "")))
                if item_id and item_id in avg_by_item:
                    cost += (avg_by_item[item_id] or Decimal("0")) * Decimal(str(line.get("qty", 1)))
    fees_total = sum(buckets.values(), Decimal("0"))
    profit = revenue - fees_total - cost
    return {"date_from": date_from, "date_to": date_to,
            "orders_count": len(in_period),
            "revenue": str(revenue),
            **{key: str(buckets[name]) for key, name in (
                ("fees", "Комиссия WB"), ("logistics", "Логистика WB"),
                ("storage", "Хранение WB"), ("penalties", "Штрафы WB"),
                ("tax", "Налог WB"), ("other", "WB: Прочее"))},
            "fees_total": str(fees_total),
            "cost": str(cost), "include_cost": include_cost,
            "profit": str(profit)}


@router.get("/payments", response_model=list[PaymentOut] | Page[PaymentOut])
def list_payments(user: User = Depends(require_module("integrations", "ro")),
                  db: Session = Depends(get_db),
                  status: str | None = None, provider: str | None = None,
                  scoped: CompanyScoped = None,
                  page: PageParams = Depends(page_params)):
    rw = module_level(db, user.role, "integrations") == "rw"
    query = select(m.OnlinePayment).where(
        m.OnlinePayment.company_id == scoped).order_by(m.OnlinePayment.created_at.desc())
    if status is not None:
        query = query.where(m.OnlinePayment.status == status)
    if provider is not None:
        query = query.where(m.OnlinePayment.provider == provider)
    if page.paginated:
        total = db.scalar(select(func.count()).select_from(
            query.order_by(None).subquery())) or 0
        paged = query.offset(page.offset or 0)
        if page.limit:
            paged = paged.limit(page.limit)
        return {"items": [_payment_out(p, rw) for p in db.scalars(paged).all()],
                "total": int(total)}
    return [_payment_out(p, rw) for p in db.scalars(query.limit(200)).all()]


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: uuid.UUID, user: User = Depends(require_module("integrations", "ro")),
                db: Session = Depends(get_db)):
    payment = db.get(m.OnlinePayment, payment_id)
    if payment is None:
        raise HTTPException(404, "Payment not found")
    rw = module_level(db, user.role, "integrations") == "rw"
    out = _payment_out(payment, rw)
    run = db.scalar(select(m.FlowRun).where(m.FlowRun.payment_id == payment.id))
    if run is not None:
        out.lines = (out.lines or []) + [{"_flow": {
            "status": run.status, "step": run.step, "attempts": run.attempts,
            "error": run.error[:200],
            "context": {k: v for k, v in (run.context or {}).items()},
        }}]
    return out


@router.post("/payments/{payment_id}/retry")
def retry_payment(payment_id: uuid.UUID,
                  user: User = Depends(require_module("integrations")), db: Session = Depends(get_db)):
    """Повторить флоу с последнего успешного шага (после правки маппинга/
    пополнения кодов). Шаги идемпотентны — созданное не дублируется."""
    payment = db.get(m.OnlinePayment, payment_id)
    if payment is None:
        raise HTTPException(404, "Payment not found")
    # §8: delivery_failed — учёт done, retry повторяет только notify
    if payment.status == "processed" and payment.error_reason != "delivery_failed":
        raise HTTPException(409, "Payment is already processed")
    connection = db.get(m.Connection, payment.connection_id)
    recipe, api = _flow_recipe_and_api(db, connection)
    if recipe is None or api is None:
        raise HTTPException(409, "sales_flow recipe/api not configured")
    from . import sales_flow as flow_mod
    run = flow_mod.run_sales_flow(db, payment=payment, recipe=recipe, api=api)
    return {"payment_status": payment.status, "flow_status": run.status,
            "step": run.step, "error": run.error[:300]}


@router.post("/webhook-events/{event_id}/reprocess")
def reprocess_webhook_event(event_id: uuid.UUID,
                            user: User = Depends(require_module("integrations")),
                            db: Session = Depends(get_db)):
    """Переобработать вебхук: нормализация + запуск флоу (дубликаты
    платежей гасятся UNIQUE)."""
    event = db.get(m.WebhookEvent, event_id)
    if event is None:
        raise HTTPException(404, "Webhook event not found")
    if event.status == "invalid":
        raise HTTPException(409, "Event is invalid: verification failed")
    if event.connection_id is None:
        raise HTTPException(422, "Event has no provider connection")
    connection = db.get(m.Connection, event.connection_id)
    from .crypto import decrypt_dict
    connector = connector_registry.build(
        connection.connector_code, connection.config,
        decrypt_dict(connection.credentials_enc),
    )
    from . import sales_flow as flow_mod
    payment = flow_mod.normalize_payment(db, connection=connection,
                                         connector=connector, event=event)
    if payment is None:
        raise HTTPException(422, "Payload has no payment id")
    recipe, api = _flow_recipe_and_api(db, connection)
    if recipe is None or api is None:
        return {"payment_id": str(payment.id), "flow": "recipe/api not configured"}
    run = flow_mod.run_sales_flow(db, payment=payment, recipe=recipe, api=api)
    return {"payment_id": str(payment.id), "flow_status": run.status,
            "step": run.step, "error": run.error[:300]}


# ---------- Маппинги сайт-товар → номенклатура (§5.1) ----------

class ItemMappingIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "connection_id": "uuid-yookassa (или null — глобальный)",
        "external_item_id": "site-sku-1", "sku": "DIGI-1", "item_id": "uuid",
    }}}

    connection_id: uuid.UUID | None = None
    external_item_id: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=100)
    item_id: uuid.UUID


class ItemMappingOut(ItemMappingIn):
    id: uuid.UUID
    is_active: bool
    created_at: object = None

    model_config = {**ItemMappingIn.model_config, "from_attributes": True}


@router.get("/item-mappings", response_model=list[ItemMappingOut] | Page[ItemMappingOut])
def list_item_mappings(user: User = Depends(require_module("integrations", "ro")),
                       db: Session = Depends(get_db), scoped: CompanyScoped = None,
                       page: PageParams = Depends(page_params)):
    return page.apply(db, select(m.ItemMapping).where(
        m.ItemMapping.company_id == scoped).order_by(m.ItemMapping.created_at.desc()))


@router.post("/item-mappings", response_model=ItemMappingOut, status_code=201)
def create_item_mapping(body: ItemMappingIn,
                        user: User = Depends(require_module("integrations")),
                        db: Session = Depends(get_db), scoped: CompanyScoped = None):
    existing = db.scalar(select(m.ItemMapping).where(
        m.ItemMapping.company_id == scoped,
        m.ItemMapping.connection_id == body.connection_id
        if body.connection_id else m.ItemMapping.connection_id.is_(None),
        m.ItemMapping.external_item_id == body.external_item_id,
    ))
    if existing is not None:
        raise HTTPException(409, "Mapping already exists — use PATCH")
    mapping = m.ItemMapping(**body.model_dump(), company_id=scoped)
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.patch("/item-mappings/{mapping_id}", response_model=ItemMappingOut)
def patch_item_mapping(mapping_id: uuid.UUID, body: dict,
                       user: User = Depends(require_module("integrations")),
                       db: Session = Depends(get_db)):
    mapping = db.get(m.ItemMapping, mapping_id)
    if mapping is None:
        raise HTTPException(404, "Mapping not found")
    for field in ("sku", "item_id", "is_active", "connection_id"):
        if field in body:
            setattr(mapping, field, body[field])
    if "external_item_id" in body:
        mapping.external_item_id = str(body["external_item_id"])
    db.commit()
    db.refresh(mapping)
    return mapping


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


@router.get("/notification-rules", response_model=list[NotificationRuleOut] | Page[NotificationRuleOut])
def list_notification_rules(user: User = Depends(require_module("integrations")),
                           db: Session = Depends(get_db), scoped: CompanyScoped = None,
                           page: PageParams = Depends(page_params)):
    return page.apply(db, select(m.NotificationRule).where(
        m.NotificationRule.company_id == scoped
    ).order_by(m.NotificationRule.created_at))


@router.post("/notification-rules", response_model=NotificationRuleOut, status_code=201)
def create_notification_rule(body: NotificationRuleIn, user: User = Depends(require_module("integrations")),
                             db: Session = Depends(get_db), scoped: CompanyScoped = None):
    from src.modules.integrations.notify import NOTIFY_EVENTS

    if body.event_name not in NOTIFY_EVENTS:
        raise HTTPException(422, f"event must be one of {list(NOTIFY_EVENTS)}")
    rule = m.NotificationRule(**body.model_dump(), company_id=scoped)
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


@router.post("/recipes/{recipe_id}/publish")
def publish_recipe(recipe_id: uuid.UUID,
                   user: User = Depends(require_module("integrations")),
                   db: Session = Depends(get_db)):
    recipe = db.get(m.Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(404, "Recipe not found")
    recipe.is_published = True
    db.commit()
    return {"ok": True}


@router.get("/recipes")
def list_recipes(user: User = Depends(require_module("integrations", "ro")), db: Session = Depends(get_db), published_only: bool = False):
    query = select(m.Recipe)
    if published_only:
        query = query.where(m.Recipe.is_published.is_(True))
    return db.scalars(query).all()
