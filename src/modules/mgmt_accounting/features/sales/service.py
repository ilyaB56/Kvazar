"""Сервисный слой фичи sales: заказы клиентов, отгрузки, оплаты, маржа.

Инварианты (resources-core §2–§4):
- заказ: курс заморожен при создании (§2.6), номер ЗК-… при confirm;
  crm_deal_id — префилл контрагента из сделки (чтение mini_crm.deals
  одним SQL-запросом без импорта модуля, UUID-ссылка без FK);
- отгрузка: проведение = контроль остатка (insufficient_stock, настройка
  allow_negative_stock уже в inventory) + движения «склад → Клиент» +
  серийники sold (явный список или FIFO-автовыбор старейших) + списание
  по средней (§4) + статус заказа + событие; приём сверх остатка строки
  заказа — 422 over_shipment;
- сторно отгрузки (unpost): парные движения «Клиент → склад», серийники
  обратно in_stock; средняя не откатывается;
- оплата: входящая transaction, категория «Продажи» (авто-seed),
  контрагент наследуется, source-ссылка sales_order в dimensions.

События (ADR-002, деньги/количества строками): acc.sales.order.created,
acc.sales.order.confirmed, acc.sales.shipped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.core import events
from src.core.versioning import record_version
from src.modules.mgmt_accounting import models as acc
from src.modules.mgmt_accounting import service as acc_service
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service
from src.modules.mgmt_accounting.features.sales import models as m
from src.modules.mgmt_accounting.service import AccountingError, quantize2

SALES_CATEGORY = "Продажи"
SOURCE_TYPE = "sales_order"
DEFAULT_LOCATIONS = {"physical": "Основной склад", "digital": "Цифровой склад"}


def _get_counterparty(db: Session, counterparty_id: uuid.UUID) -> acc.Counterparty:
    counterparty = db.get(acc.Counterparty, counterparty_id)
    if counterparty is None or not counterparty.is_active:
        raise AccountingError(422, f"Unknown or inactive counterparty: {counterparty_id}")
    return counterparty


def _deal_counterparty(db: Session, deal_id: uuid.UUID) -> uuid.UUID:
    """Контрагент сделки mini_crm: одно чтение по UUID без импорта модуля
    (крест-модульные ссылки — по UUID, как контрагент в самой CRM)."""
    row = db.execute(
        text("SELECT counterparty_id FROM mini_crm.deals WHERE id = :deal_id"),
        {"deal_id": deal_id},
    ).first()
    if row is None or row[0] is None:
        raise AccountingError(422, f"deal_not_found_or_no_counterparty: {deal_id}")
    return row[0]


# ---------- Заказы ----------

def create_order(db: Session, *, user_id: uuid.UUID, data: dict) -> m.SalesOrder:
    counterparty_id = data.get("counterparty_id")
    if counterparty_id is None and data.get("crm_deal_id") is not None:
        counterparty_id = _deal_counterparty(db, data["crm_deal_id"])
    if counterparty_id is None:
        raise AccountingError(422, "counterparty_id required (или crm_deal_id для префилла)")
    _get_counterparty(db, counterparty_id)
    currency = data.get("currency") or acc_service.BASE_CURRENCY
    rate = acc_service.rate_for(db, date.today(), currency)
    lines_data = data.get("lines") or []
    if not lines_data:
        raise AccountingError(422, "order_lines_required")

    order = m.SalesOrder(
        counterparty_id=counterparty_id,
        crm_deal_id=data.get("crm_deal_id"),
        status="draft",
        currency=currency,
        rate=rate,
        amount=Decimal(0),
        amount_base=Decimal(0),
        note=data.get("note", ""),
        created_by=user_id,
    )
    db.add(order)
    db.flush()

    amount = Decimal(0)
    amount_base = Decimal(0)
    for line in lines_data:
        item = inv_service.get_item(db, line["item_id"])
        qty = inv_service.quantize4(line["qty"])
        unit_price = inv_service.quantize4(line["unit_price"])
        if qty <= 0 or unit_price < 0:
            raise AccountingError(422, "qty must be positive, unit_price must be >= 0")
        line_amount = inv_service.quantize4(qty * unit_price)
        db.add(m.SalesOrderLine(
            order_id=order.id,
            item_id=item.id,
            qty=qty,
            unit_price=unit_price,
            amount=line_amount,
            reserved_qty=Decimal(0),
        ))
        amount += line_amount
        amount_base += quantize2(line_amount * rate)
    order.amount = amount
    order.amount_base = amount_base
    db.flush()
    events.publish(db, "acc.sales.order.created", order_payload(order))
    return order


def confirm_order(db: Session, order: m.SalesOrder, *, user_id: uuid.UUID) -> m.SalesOrder:
    if order.status != "draft":
        raise AccountingError(409, f"Only draft orders can be confirmed (status={order.status})")
    order.status = "confirmed"
    order.number = acc_service.next_doc_number(db, m.ORDER_DOC_TYPE, date.today())
    # резерв v1: подтверждённый заказ резервирует полные строки (§3.4)
    for line in _order_lines(db, order.id):
        line.reserved_qty = line.qty
    order.updated_at = datetime.now(UTC)
    db.flush()
    record_version(db, "acc.sales.order", str(order.id), user_id,
                   {"status": {"old": "draft", "new": "confirmed"}, "number": {"new": order.number}})
    events.publish(db, "acc.sales.order.confirmed", order_payload(order))
    return order


def cancel_order(db: Session, order: m.SalesOrder, *, user_id: uuid.UUID) -> m.SalesOrder:
    shipped = _shipped_qty_by_item(db, order.id)
    if any(qty > 0 for qty in shipped.values()):
        raise AccountingError(422, "order_has_shipments: cancellation is not allowed")
    if order.status not in ("draft", "confirmed"):
        raise AccountingError(409, f"Only draft/confirmed orders can be cancelled (status={order.status})")
    old_status = order.status
    order.status = "cancelled"
    for line in _order_lines(db, order.id):
        line.reserved_qty = Decimal(0)
    order.updated_at = datetime.now(UTC)
    db.flush()
    record_version(db, "acc.sales.order", str(order.id), user_id,
                   {"status": {"old": old_status, "new": "cancelled"}})
    return order


def order_payload(order: m.SalesOrder) -> dict[str, Any]:
    return {
        "order_id": str(order.id),
        "number": order.number,
        "status": order.status,
        "counterparty_id": str(order.counterparty_id),
        "crm_deal_id": str(order.crm_deal_id) if order.crm_deal_id else None,
        "amount": str(order.amount),
        "currency": order.currency,
        "rate": str(order.rate),
        "amount_base": str(order.amount_base),
    }


def _order_lines(db: Session, order_id: uuid.UUID) -> list[m.SalesOrderLine]:
    return db.scalars(
        select(m.SalesOrderLine).where(m.SalesOrderLine.order_id == order_id)
    ).all()


def _shipped_qty_by_item(db: Session, order_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Отгружено по заказу: Σ строк проведённых не-сторно отгрузок."""
    rows = db.execute(
        select(m.ShipmentLine.item_id, func.sum(m.ShipmentLine.qty))
        .join(m.Shipment, m.Shipment.id == m.ShipmentLine.shipment_id)
        .where(
            m.Shipment.sales_order_id == order_id,
            m.Shipment.status == "posted",
            m.Shipment.is_stornoed.is_(False),
        )
        .group_by(m.ShipmentLine.item_id)
    ).all()
    return {item_id: Decimal(qty) for item_id, qty in rows}


def _remaining_by_item(db: Session, order_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    ordered = {line.item_id: line.qty for line in _order_lines(db, order_id)}
    shipped = _shipped_qty_by_item(db, order_id)
    return {item_id: qty - shipped.get(item_id, Decimal(0)) for item_id, qty in ordered.items()}


def _recompute_order_status(db: Session, order: m.SalesOrder) -> None:
    if order.status in ("cancelled", "draft"):
        return
    ordered = {line.item_id: line.qty for line in _order_lines(db, order.id)}
    shipped = _shipped_qty_by_item(db, order.id)
    fully = all(shipped.get(item_id, Decimal(0)) >= qty for item_id, qty in ordered.items())
    partially = any(qty > 0 for qty in shipped.values())
    new_status = "shipped" if fully else ("partially_shipped" if partially else "confirmed")
    if new_status != order.status:
        record_version(db, "acc.sales.order", str(order.id), order.created_by,
                       {"status": {"old": order.status, "new": new_status}}, reason="shipment posted")
        order.status = new_status
        order.updated_at = datetime.now(UTC)
    db.flush()


# ---------- Отгрузки ----------

def create_shipment(db: Session, *, user_id: uuid.UUID, data: dict) -> m.Shipment:
    order = db.get(m.SalesOrder, data["sales_order_id"])
    if order is None:
        raise AccountingError(422, "Unknown sales order")
    if order.status not in ("confirmed", "partially_shipped"):
        raise AccountingError(422, f"order_not_shippable: status={order.status}")
    if not data.get("lines"):
        raise AccountingError(422, "shipment_lines_required")

    shipment = m.Shipment(
        sales_order_id=order.id,
        status="draft",
        counterparty_doc=data.get("counterparty_doc"),
        note=data.get("note", ""),
        moved_at=data.get("moved_at") or date.today(),
        created_by=user_id,
    )
    db.add(shipment)
    db.flush()

    remaining = _remaining_by_item(db, order.id)
    price_by_item = {line.item_id: line.unit_price for line in _order_lines(db, order.id)}
    for line in data["lines"]:
        item = inv_service.get_item(db, line["item_id"])
        qty = inv_service.quantize4(line["qty"])
        codes = [str(code).strip() for code in line.get("serial_codes") or []]
        if item.tracking == "serial" and codes and qty != len(codes):
            raise AccountingError(
                422, f"serial_qty_mismatch: qty={qty} but {len(codes)} codes for {item.sku}"
            )
        if qty <= 0:
            raise AccountingError(422, "qty must be positive")
        left = remaining.get(item.id, Decimal(0))
        if qty > left:
            raise AccountingError(
                422, f"over_shipment: {item.sku} ordered left {left}, got {qty}"
            )
        remaining[item.id] = left - qty
        db.add(m.ShipmentLine(
            shipment_id=shipment.id,
            item_id=item.id,
            location_id=line.get("location_id"),
            qty=qty,
            unit_price=price_by_item.get(item.id),
            serial_codes=codes or None,
        ))
    db.flush()
    return shipment


def _default_location(db: Session, item: inv.Item) -> inv.Location:
    name = DEFAULT_LOCATIONS[item.kind]
    location = db.scalar(select(inv.Location).where(inv.Location.name == name))
    if location is None:
        raise AccountingError(422, f"Default location not found: {name}")
    return location


def _fifo_serials(
    db: Session, item: inv.Item, location: inv.Location, qty: Decimal
) -> list[inv.ItemSerial]:
    """FIFO-автовыбор: старейшие по поступлению in_stock серийники локации
    (внутри дня — по времени приёмки, т.е. created_at движения)."""
    serials = db.execute(
        select(inv.ItemSerial)
        .join(inv.StockMove, inv.StockMove.id == inv.ItemSerial.received_move_id)
        .where(
            inv.ItemSerial.item_id == item.id,
            inv.ItemSerial.status == "in_stock",
            inv.ItemSerial.location_id == location.id,
        )
        .order_by(inv.ItemSerial.received_at, inv.StockMove.created_at, inv.ItemSerial.id)
    ).scalars().all()
    if len(serials) < qty:
        raise AccountingError(
            422, f"insufficient_stock: only {len(serials)} serials of {item.sku} on «{location.name}»"
        )
    return serials[: int(qty)]


def post_shipment(db: Session, shipment: m.Shipment) -> m.Shipment:
    """Проведение: контроль минуса + движения «склад → Клиент» + серийники
    sold (FIFO или явно) + списание по средней + статус заказа (§3.4)."""
    if shipment.is_stornoed:
        raise AccountingError(422, "Shipment is stornoed and cannot be posted again")
    if shipment.status == "posted":
        raise AccountingError(409, f"Shipment {shipment.number or shipment.id} is already posted")
    order = db.get(m.SalesOrder, shipment.sales_order_id)
    transit = db.scalar(select(inv.Location).where(
        inv.Location.name == "Клиент", inv.Location.is_transit
    ))
    if transit is None:
        raise AccountingError(422, "System transit location not found: Клиент")

    remaining = _remaining_by_item(db, order.id)
    lines = db.scalars(select(m.ShipmentLine).where(
        m.ShipmentLine.shipment_id == shipment.id
    )).all()
    if not lines:
        raise AccountingError(422, "shipment_lines_required")

    revenue_base = Decimal(0)
    for line in lines:
        item = inv_service.get_item(db, line.item_id)
        location = (
            inv_service.get_location(db, line.location_id)
            if line.location_id else _default_location(db, item)
        )
        if location.is_transit:
            raise AccountingError(422, "shipment_from_transit_location_is_not_allowed")
        inv_service._check_kind_compat(item, location)
        left = remaining.get(item.id, Decimal(0))
        if line.qty > left:
            raise AccountingError(422, f"over_shipment: {item.sku} ordered left {left}")
        remaining[item.id] = left - line.qty

        codes: list[str] = list(line.serial_codes or [])
        if item.tracking == "serial":
            if not codes:
                codes = [
                    inv_service.serial_code(serial)
                    for serial in _fifo_serials(db, item, location, line.qty)
                ]
            elif len(codes) != line.qty:
                raise AccountingError(
                    422, f"serial_qty_mismatch: qty={line.qty} but {len(codes)} codes"
                )
        inv_service._apply_issue(
            db, item=item, from_location=location, qty=line.qty,
            user_id=shipment.created_by, to_location=transit,
            counterparty_id=order.counterparty_id, source_type="shipment",
            source_id=shipment.id, moved_at=shipment.moved_at,
            note=shipment.counterparty_doc or "", serial_codes=codes or None,
        )
        if line.unit_price is not None and order.rate is not None:
            line.amount_base = quantize2(line.qty * line.unit_price * order.rate)
            revenue_base += line.amount_base

    shipment.status = "posted"
    shipment.number = acc_service.next_doc_number(db, m.SHIPMENT_DOC_TYPE, shipment.moved_at)
    shipment.posted_at = datetime.now(UTC)
    # резерв v1: отгруженное снимается со строки заказа
    _apply_reserve_delta(db, order, shipment, restore=False)
    db.flush()
    _recompute_order_status(db, order)
    events.publish(db, "acc.sales.shipped", {
        "shipment_id": str(shipment.id),
        "number": shipment.number,
        "sales_order_id": str(order.id),
        "counterparty_id": str(order.counterparty_id),
        "moved_at": shipment.moved_at.isoformat(),
        "revenue_base": str(quantize2(revenue_base)),
    })
    return shipment


def unpost_shipment(db: Session, shipment: m.Shipment, *, user_id: uuid.UUID, reason: str) -> m.Shipment:
    """Сторно отгрузки: парные движения «Клиент → склад», серийники sold →
    in_stock на складе; себестоимость/средняя не откатываются (как у
    сторно денежных документов). Товар у клиента — вернуть в учёт можно
    всегда, но только один раз (is_stornoed)."""
    if shipment.status != "posted":
        raise AccountingError(422, "Only posted shipments can be unposted")
    if shipment.is_stornoed:
        raise AccountingError(409, "Shipment is already stornoed")

    moves = db.scalars(select(inv.StockMove).where(
        inv.StockMove.source_type == "shipment", inv.StockMove.source_id == shipment.id
    )).all()
    order = db.get(m.SalesOrder, shipment.sales_order_id)
    transit = db.scalar(select(inv.Location).where(
        inv.Location.name == "Клиент", inv.Location.is_transit
    ))

    for move in moves:
        item = db.get(inv.Item, move.item_id)
        location = inv_service.get_location(db, move.from_location_id)
        # серийники выдачи возвращаются на склад в in_stock
        serials = db.scalars(select(inv.ItemSerial).where(
            inv.ItemSerial.sold_move_id == move.id
        )).all()
        for serial in serials:
            if serial.status != "sold":
                raise AccountingError(
                    422, f"has_subsequent_moves: serial {serial.code_hash[:8]}… is not sold"
                )
        inv_service._apply_receipt(
            db, item=item, to_location=location, qty=move.qty,
            unit_cost=None,  # средняя не откатывается
            user_id=user_id, from_location=transit,
            counterparty_id=order.counterparty_id, source_type="shipment_storno",
            source_id=shipment.id, moved_at=date.today(),
            note=f"Сторно отгрузки {shipment.number}", serial_codes=None,
        )
        for serial in serials:
            serial.status = "in_stock"
            serial.sold_move_id = None
            serial.location_id = location.id

    shipment.is_stornoed = True
    for line in db.scalars(select(m.ShipmentLine).where(
        m.ShipmentLine.shipment_id == shipment.id
    )).all():
        line.amount_base = None
    _apply_reserve_delta(db, order, shipment, restore=True)
    db.flush()
    record_version(db, "acc.sales.shipment", str(shipment.id), user_id,
                   {"is_stornoed": {"old": False, "new": True}}, reason=reason)
    _recompute_order_status(db, order)
    return shipment


def _apply_reserve_delta(db: Session, order: m.SalesOrder, shipment: m.Shipment, *, restore: bool) -> None:
    """Резерв v1: отгрузка снимает резерв строки, сторно возвращает."""
    lines = db.scalars(select(m.ShipmentLine).where(
        m.ShipmentLine.shipment_id == shipment.id
    )).all()
    reserved = {line.item_id: line for line in _order_lines(db, order.id)}
    for line in lines:
        order_line = reserved.get(line.item_id)
        if order_line is None:
            continue
        if restore:
            order_line.reserved_qty += line.qty
        else:
            order_line.reserved_qty = max(Decimal(0), order_line.reserved_qty - line.qty)
    db.flush()


# ---------- Оплата ----------

def _sales_category(db: Session) -> acc.Category:
    """Категория «Продажи» (доход) — авто-seed при первой оплате (§3.4)."""
    category = db.scalar(select(acc.Category).where(
        acc.Category.name == SALES_CATEGORY, acc.Category.kind == "income"
    ))
    if category is None:
        category = acc.Category(name=SALES_CATEGORY, kind="income")
        db.add(category)
        db.flush()
    return category


def pay_order(db: Session, *, order: m.SalesOrder, user_id: uuid.UUID, data: dict):
    """Входящая транзакция с категорией «Продажи», контрагентом заказа и
    source-ссылкой; частичные оплаты — несколько транзакций."""
    if order.status in ("draft", "cancelled"):
        raise AccountingError(422, f"order_not_payable: status={order.status}")
    account = db.get(acc.Account, data["account_id"])
    if account is None or not account.is_active:
        raise AccountingError(422, f"Unknown or inactive account: {data['account_id']}")
    category = _sales_category(db)
    txn = acc_service.create_transaction(db, user_id=user_id, data={
        "kind": "income",
        "operated_at": data.get("operated_at") or date.today(),
        "amount": data["amount"],
        "currency": account.currency,
        "account_id": account.id,
        "category_id": category.id,
        "counterparty_id": order.counterparty_id,
        "description": data.get("description") or f"Оплата заказа {order.number or order.id}",
        "dimensions": {"source_type": SOURCE_TYPE, "source_id": str(order.id)},
    })
    acc_service.post_transaction(db, txn)
    return txn


# ---------- Отчёты ----------

def sales_report(
    db: Session, date_from: date, date_to: date, counterparty_id: uuid.UUID | None
) -> dict[str, Any]:
    """Продажи за период (§3.6): выручка по отгрузкам, себестоимость
    списаний (движения к «Клиенту»), маржа = выручка − себестоимость."""
    orders = db.scalars(
        select(m.SalesOrder).where(
            func.date(m.SalesOrder.created_at) >= date_from,
            func.date(m.SalesOrder.created_at) <= date_to,
        ).order_by(m.SalesOrder.created_at)
    ).all()
    if counterparty_id is not None:
        orders = [o for o in orders if o.counterparty_id == counterparty_id]

    shipments = db.scalars(
        select(m.Shipment).where(
            m.Shipment.status == "posted",
            m.Shipment.is_stornoed.is_(False),
            m.Shipment.moved_at >= date_from,
            m.Shipment.moved_at <= date_to,
        ).order_by(m.Shipment.moved_at)
    ).all()

    counterparties = {c.id: c for c in db.scalars(select(acc.Counterparty)).all()}
    items = {i.id: i for i in db.scalars(select(inv.Item)).all()}

    by_item: dict[uuid.UUID, dict[str, Decimal]] = {}
    by_counterparty: dict[uuid.UUID, dict[str, Decimal]] = {}
    revenue_total = Decimal(0)
    cogs_total = Decimal(0)

    for shipment in shipments:
        order = db.get(m.SalesOrder, shipment.sales_order_id)
        if counterparty_id is not None and order.counterparty_id != counterparty_id:
            continue
        cp_bucket = by_counterparty.setdefault(order.counterparty_id, {
            "orders_amount_base": Decimal(0), "revenue_base": Decimal(0),
            "cogs_base": Decimal(0), "paid_amount_base": Decimal(0),
        })
        # себестоимость — по фактическим расходным движениям отгрузки;
        # строки и движения группируются по товару (строк с одним товаром
        # в одной отгрузке может быть несколько)
        cogs_by_item: dict[uuid.UUID, Decimal] = {}
        for move in db.scalars(select(inv.StockMove).where(
            inv.StockMove.source_type == "shipment",
            inv.StockMove.source_id == shipment.id,
        )).all():
            cost = quantize2(move.qty * move.unit_cost) if move.unit_cost else Decimal(0)
            cogs_by_item[move.item_id] = cogs_by_item.get(move.item_id, Decimal(0)) + cost
        grouped: dict[uuid.UUID, dict[str, Decimal]] = {}
        for line in db.scalars(select(m.ShipmentLine).where(
            m.ShipmentLine.shipment_id == shipment.id
        )).all():
            g = grouped.setdefault(line.item_id, {"qty": Decimal(0), "revenue": Decimal(0)})
            g["qty"] += line.qty
            g["revenue"] += line.amount_base or Decimal(0)
        for item_id, g in grouped.items():
            revenue = g["revenue"]
            cogs = cogs_by_item.get(item_id, Decimal(0))
            revenue_total += revenue
            cogs_total += cogs
            cp_bucket["revenue_base"] += revenue
            cp_bucket["cogs_base"] += cogs
            bucket = by_item.setdefault(item_id, {
                "qty": Decimal(0), "revenue_base": Decimal(0), "cogs_base": Decimal(0),
            })
            bucket["qty"] += g["qty"]
            bucket["revenue_base"] += revenue
            bucket["cogs_base"] += cogs

    orders_amount = sum((o.amount_base for o in orders), Decimal(0))
    for order in orders:
        cp_bucket = by_counterparty.setdefault(order.counterparty_id, {
            "orders_amount_base": Decimal(0), "revenue_base": Decimal(0),
            "cogs_base": Decimal(0), "paid_amount_base": Decimal(0),
        })
        cp_bucket["orders_amount_base"] += order.amount_base
        cp_bucket["paid_amount_base"] += sum((
            t.amount_base for t in db.scalars(
                select(acc.Transaction).where(
                    acc.Transaction.status == "posted",
                    acc.Transaction.kind == "income",
                    acc.Transaction.dimensions["source_type"].astext == SOURCE_TYPE,
                    acc.Transaction.dimensions["source_id"].astext == str(order.id),
                )
            ).all()
        ), Decimal(0))

    return {
        "date_from": date_from,
        "date_to": date_to,
        "counterparty_id": counterparty_id,
        "orders": {"count": len(orders), "amount_base": str(quantize2(orders_amount))},
        "shipments": {
            "count": len(shipments),
            "revenue_base": str(quantize2(revenue_total)),
            "cogs_base": str(quantize2(cogs_total)),
            # маржа = продажи − себестоимость списаний (§3.6)
            "margin_base": str(quantize2(revenue_total - cogs_total)),
        },
        "by_item": [
            {
                "item_id": str(item_id),
                "sku": items[item_id].sku if item_id in items else "?",
                "item_name": items[item_id].name if item_id in items else "?",
                "qty": str(inv_service.quantize4(bucket["qty"])),
                "revenue_base": str(quantize2(bucket["revenue_base"])),
                "cogs_base": str(quantize2(bucket["cogs_base"])),
                "margin_base": str(quantize2(bucket["revenue_base"] - bucket["cogs_base"])),
            }
            for item_id, bucket in sorted(by_item.items(), key=lambda kv: str(kv[0]))
        ],
        "by_counterparty": [
            {
                "counterparty_id": str(cp_id),
                "name": counterparties[cp_id].name if cp_id in counterparties else "?",
                "orders_amount_base": str(quantize2(bucket["orders_amount_base"])),
                "revenue_base": str(quantize2(bucket["revenue_base"])),
                "cogs_base": str(quantize2(bucket["cogs_base"])),
                "margin_base": str(quantize2(bucket["revenue_base"] - bucket["cogs_base"])),
                "paid_amount_base": str(quantize2(bucket["paid_amount_base"])),
            }
            for cp_id, bucket in sorted(by_counterparty.items(), key=lambda kv: str(kv[0]))
        ],
    }
