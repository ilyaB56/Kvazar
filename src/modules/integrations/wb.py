"""Куратор синхронизации Wildberries (wb-connector-spec §3.2–3.3).

Паттерн ozon.run_ozon_sync; отличия: заказы по uid, линии с nm_id/
vendor_code, транзакции с типами commission/logistics/storage/penalty/
tax/payment. Этап B (заказы → sales_orders) и C (комиссии → расходы)
добавлены поверх (см. wb_docs).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from src.core import events
from src.modules.integrations import models as m

logger = logging.getLogger(__name__)


def run_wb_sync(db, *, connection, connector, kind: str = "", job=None) -> dict:
    """Прогон одного задания (kind — из job.endpoint или явно)."""
    kind = (kind or (job.endpoint if job is not None else "") or "products").strip()
    if kind == "products":
        return _sync_products(db, connection, connector)
    if kind == "stocks":
        return _sync_stocks(db, connection, connector)
    if kind == "orders":
        return _sync_orders(db, connection, connector)
    if kind == "transactions":
        return _sync_transactions(db, connection, connector)
    return {"ok": False, "error": f"unknown wb kind: {kind}"}


def _sync_products(db, connection, connector) -> dict:
    """Каталог карточек: upsert по (connection, nm_id) — дублей нет."""
    result = connector.fetch("products")
    if not result.ok:
        return {"ok": False, "error": result.error}
    now = datetime.now(UTC)
    known = {row.nm_id: row for row in db.scalars(select(m.WBProduct).where(
        m.WBProduct.connection_id == connection.id)).all()}
    created = updated = 0
    for item in result.data or []:
        nm_id = str(item.get("nm_id", ""))
        if not nm_id:
            continue
        row = known.get(nm_id)
        price = _dec(item.get("price"))
        if row is None:
            db.add(m.WBProduct(
                company_id=connection.company_id, connection_id=connection.id,
                nm_id=nm_id, vendor_code=str(item.get("vendor_code", ""))[:200] or None,
                name=str(item.get("name", ""))[:500], price=price, fetched_at=now))
            created += 1
        else:
            row.name = str(item.get("name", ""))[:500] or row.name
            if item.get("vendor_code"):
                row.vendor_code = str(item["vendor_code"])[:200]
            if price is not None:
                row.price = price
            row.fetched_at = now
            updated += 1
    db.commit()
    return {"ok": True, "items_in": created + updated, "created": created,
            "updated": updated}


def _sync_stocks(db, connection, connector) -> dict:
    """Снапшот остатков WB: перезапись за прогон (чужой склад)."""
    result = connector.fetch("stocks")
    if not result.ok:
        return {"ok": False, "error": result.error}
    now = datetime.now(UTC)
    db.query(m.WBStock).filter(
        m.WBStock.connection_id == connection.id).delete()
    count = 0
    for item in result.data or []:
        db.add(m.WBStock(
            company_id=connection.company_id, connection_id=connection.id,
            nm_id=str(item.get("nm_id", "")),
            warehouse_id=str(item.get("warehouse_id", ""))[:100],
            qty=int(item.get("qty", 0) or 0), fetched_at=now))
        count += 1
    db.commit()
    return {"ok": True, "items_in": count, "created": count}


def _sync_orders(db, connection, connector) -> dict:
    """Заказы → wb_orders (+ sales_orders draft — этап B, §3.3.3)."""
    result = connector.fetch("orders")
    if not result.ok:
        return {"ok": False, "error": result.error}
    from . import wb_docs

    now = datetime.now(UTC)
    existing = {row.uid: row for row in db.scalars(
        select(m.WBOrder).where(m.WBOrder.connection_id == connection.id)).all()}
    new_orders = cancelled = created_docs = mapping_errors = 0
    for po in result.data or []:
        uid = str(po.get("uid", ""))
        if not uid:
            continue
        status = str(po.get("status", "new"))
        row = existing.get(uid)
        if row is None:
            row = m.WBOrder(
                company_id=connection.company_id, connection_id=connection.id,
                uid=uid, status=status,
                order_date=str(po.get("order_date", ""))[:30],
                amount=_dec(po.get("amount")), currency=str(po.get("currency", "RUB"))[:3],
                lines=po.get("lines") or [])
            db.add(row)
            new_orders += 1
        elif status == "cancelled" and row.status != "cancelled":
            wb_docs.on_order_cancelled(db, connection, row)
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
            created, mapping_error = wb_docs.create_sales_order(db, connection, row)
            created_docs += 1 if created else 0
            if mapping_error:
                row.mapping_error = True
                mapping_errors += 1
            elif row.mapping_error:
                row.mapping_error = False
    db.commit()
    if new_orders or cancelled:
        events.publish(db, "integration.wb.orders.synced", {
            "connection_id": str(connection.id),
            "new_orders": new_orders, "cancelled_orders": cancelled,
        })
    return {"ok": True, "items_in": new_orders, "new": new_orders,
            "cancelled": cancelled, "sales_orders": created_docs,
            "mapping_errors": mapping_errors}


def _try_expense(db, connection, row) -> None:
    """Создать расход по операции; ошибка (закрытый период и т.п.) не
    валит sync — transaction_id останется NULL, ретрай на следующем
    прогоне (§7.11)."""
    from . import wb_docs
    from .wb_docs import FlowError

    try:
        wb_docs.create_expense(db, connection, row)
    except FlowError as exc:
        logger.warning("wb expense pending (manual): op=%s err=%s",
                       row.operation_id, str(exc)[:160])


def _sync_transactions(db, connection, connector) -> dict:
    """Транзакции → wb_transactions (+ расход по операцию — этап C)."""
    result = connector.fetch("transactions")
    if not result.ok:
        return {"ok": False, "error": result.error}
    new_count = 0
    total = Decimal("0")
    for op in result.data or []:
        operation_id = str(op.get("operation_id", ""))
        if not operation_id:
            continue
        exists = db.scalar(select(m.WBTransaction).where(
            m.WBTransaction.connection_id == connection.id,
            m.WBTransaction.operation_id == operation_id))
        if exists is not None:
            # ретрай расхода: операция есть, расход не создан (§7.11);
            # payment (выплата) не проводим вовсе (§10.2)
            if exists.transaction_id is None and exists.amount \
                    and str(exists.operation_type).lower() != "payment":
                _try_expense(db, connection, exists)
            continue
        row = m.WBTransaction(
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
        # выплата (payment) — записана, но денег не проводим (§10.2)
        if str(op.get("operation_type", "")).lower() != "payment":
            _try_expense(db, connection, row)
    db.commit()
    if new_count:
        events.publish(db, "integration.wb.transactions.synced", {
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
    """Задания по умолчанию для нового wb-подключения (§3: товары/час,
    заказы/15мин, транзакции/час). Идемпотентно по endpoint."""
    plan = [
        ("WB: товары и цены", "products", "0 * * * *"),
        ("WB: заказы", "orders", "*/15 * * * *"),
        ("WB: транзакции", "transactions", "0 * * * *"),
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
