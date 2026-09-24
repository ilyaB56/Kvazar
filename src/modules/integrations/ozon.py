"""Куратор синхронизации Ozon (ozon-connector-spec §3.2–3.3).

Вызывается из run_job для заданий connection.connector_code=ozon_seller:
endpoint задания = kind (products|stocks|orders|transactions).
Пишет в ozon_* (идемпотентно), публикует события (§6), пишет SyncRun.
Этап B (заказы → sales_orders) и C (комиссии → расходы) добавлены
поверх: см. _sync_orders/_sync_transactions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from src.core import events
from src.modules.integrations import models as m

logger = logging.getLogger(__name__)


def run_ozon_sync(db, *, connection, connector, kind: str = "", job=None) -> dict:
    """Прогон одного задания (kind — из job.endpoint или явно для
    внеочередных запусков). Возвращает метрики для SyncRun."""
    kind = (kind or (job.endpoint if job is not None else "") or "products").strip()
    if kind == "products":
        return _sync_products(db, connection, connector)
    if kind == "stocks":
        return _sync_stocks(db, connection, connector)
    if kind == "orders":
        return _sync_orders(db, connection, connector)
    if kind == "transactions":
        return _sync_transactions(db, connection, connector)
    return {"ok": False, "error": f"unknown ozon kind: {kind}"}


def _sync_products(db, connection, connector) -> dict:
    """Каталог товаров+цен: upsert по (connection, offer_id) — дублей нет."""
    result = connector.fetch("products")
    if not result.ok:
        return {"ok": False, "error": result.error}
    now = datetime.now(UTC)
    known = {row.offer_id: row for row in db.scalars(select(m.OzonProduct).where(
        m.OzonProduct.connection_id == connection.id)).all()}
    created = updated = 0
    for item in result.data or []:
        offer_id = str(item.get("offer_id", ""))
        if not offer_id:
            continue
        row = known.get(offer_id)
        price = _dec(item.get("price"))
        if row is None:
            db.add(m.OzonProduct(
                company_id=connection.company_id, connection_id=connection.id,
                offer_id=offer_id, product_id=str(item.get("product_id", "")),
                name=str(item.get("name", ""))[:500], price=price,
                fetched_at=now))
            created += 1
        else:
            row.name = str(item.get("name", ""))[:500] or row.name
            row.product_id = str(item.get("product_id", "")) or row.product_id
            if price is not None:
                row.price = price
            row.fetched_at = now
            updated += 1
    db.commit()
    return {"ok": True, "items_in": created + updated, "created": created,
            "updated": updated}


def _sync_stocks(db, connection, connector) -> dict:
    """Снапшот остатков Ozon: перезапись за прогон (чужой склад)."""
    result = connector.fetch("stocks")
    if not result.ok:
        return {"ok": False, "error": result.error}
    now = datetime.now(UTC)
    db.query(m.OzonStock).filter(
        m.OzonStock.connection_id == connection.id).delete()
    count = 0
    for item in result.data or []:
        db.add(m.OzonStock(
            company_id=connection.company_id, connection_id=connection.id,
            offer_id=str(item.get("offer_id", "")),
            warehouse_id=str(item.get("warehouse_id", ""))[:100],
            qty=int(item.get("qty", 0) or 0), fetched_at=now))
        count += 1
    db.commit()
    return {"ok": True, "items_in": count, "created": count}


def _sync_orders(db, connection, connector) -> dict:
    """Заказы → ozon_orders (+ sales_orders draft — этап B, spec §3.3.3)."""
    result = connector.fetch("orders")
    if not result.ok:
        return {"ok": False, "error": result.error}
    from . import ozon_docs

    now = datetime.now(UTC)
    existing = {row.posting_number: row for row in db.scalars(
        select(m.OzonOrder).where(m.OzonOrder.connection_id == connection.id)).all()}
    new_orders = cancelled = created_docs = mapping_errors = 0
    for po in result.data or []:
        posting = str(po.get("posting_number", ""))
        if not posting:
            continue
        status = str(po.get("status", "new"))
        row = existing.get(posting)
        if row is None:
            row = m.OzonOrder(
                company_id=connection.company_id, connection_id=connection.id,
                posting_number=posting, status=status,
                order_date=str(po.get("order_date", ""))[:30],
                amount=_dec(po.get("amount")), currency=str(po.get("currency", "RUB"))[:3],
                lines=po.get("lines") or [])
            db.add(row)
            new_orders += 1
        elif status == "cancelled" and row.status != "cancelled":
            # отмена на Ozon: черновик удаляем, подтверждённый — вручную (§3.3.3)
            ozon_docs.on_order_cancelled(db, connection, row)
            row.status = "cancelled"
            row.updated_at = now
            cancelled += 1
            continue
        else:
            row.status = status
            row.updated_at = now
        db.flush()
        # автосоздание sales_orders (draft): если включено и не создан;
        # ретрай после маппинга — успех сбрасывает mapping_error (§7.4)
        if row.sales_order_id is None and row.status != "cancelled" \
                and (connection.config or {}).get("auto_create_orders") == "on":
            created, mapping_error = ozon_docs.create_sales_order(db, connection, row)
            created_docs += 1 if created else 0
            if mapping_error:
                row.mapping_error = True
                mapping_errors += 1
            elif row.mapping_error:
                row.mapping_error = False
    db.commit()
    if new_orders or cancelled:
        events.publish(db, "integration.ozon.orders.synced", {
            "connection_id": str(connection.id),
            "new_orders": new_orders, "cancelled_orders": cancelled,
        })
    return {"ok": True, "items_in": new_orders, "new": new_orders,
            "cancelled": cancelled, "sales_orders": created_docs,
            "mapping_errors": mapping_errors}


def _sync_transactions(db, connection, connector) -> dict:
    """Транзакции → ozon_transactions (+ расход по операцию — этап C)."""
    result = connector.fetch("transactions")
    if not result.ok:
        return {"ok": False, "error": result.error}
    from . import ozon_docs

    new_count = 0
    total = Decimal("0")
    for op in result.data or []:
        operation_id = str(op.get("operation_id", ""))
        if not operation_id:
            continue
        exists = db.scalar(select(m.OzonTransaction).where(
            m.OzonTransaction.connection_id == connection.id,
            m.OzonTransaction.operation_id == operation_id))
        if exists is not None:
            continue
        row = m.OzonTransaction(
            company_id=connection.company_id, connection_id=connection.id,
            operation_id=operation_id,
            operation_type=str(op.get("operation_type", ""))[:50],
            amount=_dec(op.get("amount")), items=op.get("items") or [],
            posted_at=str(op.get("posted_at", ""))[:30])
        db.add(row)
        db.flush()
        new_count += 1
        amount = _dec(op.get("amount")) or Decimal("0")
        total += amount
        # расход по статье (одна транзакция на операцию — §10.1);
        # transfer в v1 не проводим (§10.2)
        if str(op.get("operation_type", "")).lower() != "transfer":
            try:
                ozon_docs.create_expense(db, connection, row)
            except NotImplementedError:
                # этап C (комиссии → расходы) — следующий коммит
                logger.info("ozon expense skipped until stage C: op=%s", operation_id)
    db.commit()
    if new_count:
        events.publish(db, "integration.ozon.transactions.synced", {
            "connection_id": str(connection.id), "new_count": new_count,
            "total_amount": str(total),
        })
    return {"ok": True, "items_in": new_count, "new": new_count,
            "total": str(total)}


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 — битые суммы не должны валить sync
        return None


def seed_sync_jobs(db, connection) -> None:
    """Задания по умолчанию для нового ozon-подключения (§3: товары/час,
    заказы/15 мин, транзакции/час). Идемпотентно по имени."""
    plan = [
        ("Ozon: товары и цены", "products", "0 * * * *"),
        ("Ozon: заказы", "orders", "*/15 * * * *"),
        ("Ozon: транзакции", "transactions", "0 * * * *"),
    ]
    for name, endpoint, cron in plan:
        exists = db.scalar(select(m.SyncJob).where(
            m.SyncJob.connection_id == connection.id,
            m.SyncJob.endpoint == endpoint))
        if exists is not None:
            continue
        db.add(m.SyncJob(
            company_id=connection.company_id, name=name,
            connection_id=connection.id, direction="fetch",
            cron=cron, endpoint=endpoint))
    db.flush()
