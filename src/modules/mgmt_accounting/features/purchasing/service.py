"""Сервисный слой фичи purchasing: заказы, приёмки, оплаты, отчёты.

Инварианты (resources-core §2–§4):
- заказ: курс заморожен при создании (§2.6), номер по doc_sequences при
  confirm; отмена запрещена, если товар уже принят;
- приёмка: проведение = движения «Поставщик → склад» + серийники +
  пересчёт средней + статус заказа + событие, всё в одной транзакции (§2.4);
  приёмка сверх остатка по строке заказа — 422;
- сторно приёмки (unpost): парные инверсионные движения «склад → Поставщик»,
  разрешён только без последующих движений (остатка хватает, серийники
  не выданы); средняя не откатывается (как у сторно транзакций);
- оплата: исходящая transaction с категорией «Закупки товаров»
  (авто-seed при первой оплате), контрагент наследуется, source-ссылка
  в dimensions (§3.3).

События (ADR-002, деньги/количества строками): acc.purchase.order.created,
acc.purchase.order.confirmed, acc.purchase.received.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core import events
from src.core.versioning import record_version
from src.modules.mgmt_accounting import models as acc
from src.modules.mgmt_accounting import service as acc_service
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service
from src.modules.mgmt_accounting.features.purchasing import models as m
from src.modules.mgmt_accounting.service import AccountingError, quantize2

PURCHASE_CATEGORY = "Закупки товаров"
SOURCE_TYPE = "purchase_order"
DEFAULT_LOCATIONS = {"physical": "Основной склад", "digital": "Цифровой склад"}


def _get_counterparty(db: Session, counterparty_id: uuid.UUID) -> acc.Counterparty:
    counterparty = db.get(acc.Counterparty, counterparty_id)
    if counterparty is None or not counterparty.is_active:
        raise AccountingError(422, f"Unknown or inactive counterparty: {counterparty_id}")
    return counterparty


# ---------- Заказы ----------

def create_order(db: Session, *, user_id: uuid.UUID, data: dict) -> m.PurchaseOrder:
    """Черновик: номера не потребляет; курс и amount_base замораживаются
    при создании (§2.6) — валюта фиксирует экономику сделки."""
    _get_counterparty(db, data["counterparty_id"])
    currency = data["currency"]
    rate = acc_service.rate_for(db, date.today(), currency)
    lines_data = data.get("lines") or []
    if not lines_data:
        raise AccountingError(422, "order_lines_required")

    order = m.PurchaseOrder(
        counterparty_id=data["counterparty_id"],
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
        db.add(m.PurchaseOrderLine(
            order_id=order.id,
            item_id=item.id,
            qty=qty,
            unit_price=unit_price,
            amount=line_amount,
        ))
        amount += line_amount
        amount_base += quantize2(line_amount * rate)
    order.amount = amount
    order.amount_base = amount_base
    db.flush()
    events.publish(db, "acc.purchase.order.created", order_payload(order))
    return order


def confirm_order(db: Session, order: m.PurchaseOrder, *, user_id: uuid.UUID) -> m.PurchaseOrder:
    if order.status != "draft":
        raise AccountingError(409, f"Only draft orders can be confirmed (status={order.status})")
    order.status = "confirmed"
    order.number = acc_service.next_doc_number(db, m.ORDER_DOC_TYPE, date.today())
    order.updated_at = datetime.now(UTC)
    db.flush()
    record_version(db, "acc.purchase.order", str(order.id), user_id,
                   {"status": {"old": "draft", "new": "confirmed"}, "number": {"new": order.number}})
    events.publish(db, "acc.purchase.order.confirmed", order_payload(order))
    return order


def cancel_order(db: Session, order: m.PurchaseOrder, *, user_id: uuid.UUID) -> m.PurchaseOrder:
    # бизнес-правило первично: принятый товар нельзя «расзаказать»
    received = _received_qty_by_item(db, order.id)
    if any(qty > 0 for qty in received.values()):
        raise AccountingError(422, "order_has_receipts: cancellation is not allowed")
    if order.status not in ("draft", "confirmed"):
        raise AccountingError(409, f"Only draft/confirmed orders can be cancelled (status={order.status})")
    old_status = order.status
    order.status = "cancelled"
    order.updated_at = datetime.now(UTC)
    db.flush()
    record_version(db, "acc.purchase.order", str(order.id), user_id,
                   {"status": {"old": old_status, "new": "cancelled"}})
    return order


def order_payload(order: m.PurchaseOrder) -> dict[str, Any]:
    """Контракт acc.purchase.order.* — деньги строками (ADR-002)."""
    return {
        "order_id": str(order.id),
        "number": order.number,
        "status": order.status,
        "counterparty_id": str(order.counterparty_id),
        "amount": str(order.amount),
        "currency": order.currency,
        "rate": str(order.rate),
        "amount_base": str(order.amount_base),
    }


def _received_qty_by_item(db: Session, order_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Принято по заказу: Σ строк проведённых не-сторно приёмок."""
    rows = db.execute(
        select(m.ReceiptLine.item_id, func.sum(m.ReceiptLine.qty))
        .join(m.Receipt, m.Receipt.id == m.ReceiptLine.receipt_id)
        .where(
            m.Receipt.purchase_order_id == order_id,
            m.Receipt.status == "posted",
            m.Receipt.is_stornoed.is_(False),
        )
        .group_by(m.ReceiptLine.item_id)
    ).all()
    return {item_id: Decimal(qty) for item_id, qty in rows}


def _order_lines(db: Session, order_id: uuid.UUID) -> list[m.PurchaseOrderLine]:
    return db.scalars(
        select(m.PurchaseOrderLine).where(m.PurchaseOrderLine.order_id == order_id)
    ).all()


def _recompute_order_status(db: Session, order: m.PurchaseOrder) -> None:
    if order.status in ("cancelled", "draft"):
        return
    ordered = {line.item_id: line.qty for line in _order_lines(db, order.id)}
    received = _received_qty_by_item(db, order.id)
    fully = all(received.get(item_id, Decimal(0)) >= qty for item_id, qty in ordered.items())
    partially = any(qty > 0 for qty in received.values())
    new_status = "received" if fully else ("partially_received" if partially else "confirmed")
    if new_status != order.status:
        record_version(db, "acc.purchase.order", str(order.id), order.created_by,
                       {"status": {"old": order.status, "new": new_status}}, reason="receipt posted")
        order.status = new_status
        order.updated_at = datetime.now(UTC)
    db.flush()


# ---------- Приёмки ----------

def create_receipt(db: Session, *, user_id: uuid.UUID, data: dict) -> m.Receipt:
    """Черновик приёмки; для строки заказа себестоимость в базовой валюте
    по умолчанию = цена в валюте × замороженный курс заказа (§3.3)."""
    order = None
    if data.get("purchase_order_id") is not None:
        order = db.get(m.PurchaseOrder, data["purchase_order_id"])
        if order is None:
            raise AccountingError(422, "Unknown purchase order")
        if order.status not in ("confirmed", "partially_received"):
            raise AccountingError(
                422, f"order_not_confirmable: status={order.status}"
            )
    counterparty_id = data.get("counterparty_id") or (order.counterparty_id if order else None)
    if counterparty_id is None:
        raise AccountingError(422, "counterparty_id required")
    _get_counterparty(db, counterparty_id)
    if not data.get("lines"):
        raise AccountingError(422, "receipt_lines_required")

    receipt = m.Receipt(
        purchase_order_id=order.id if order else None,
        counterparty_id=counterparty_id,
        status="draft",
        counterparty_doc=data.get("counterparty_doc"),
        note=data.get("note", ""),
        moved_at=data.get("moved_at") or date.today(),
        created_by=user_id,
    )
    db.add(receipt)
    db.flush()

    remaining = _remaining_by_item(db, order) if order else {}
    for line in data["lines"]:
        inv_service.get_item(db, line["item_id"])  # 422 unknown item
        db.add(_build_receipt_line(db, receipt, order, remaining, line))
    db.flush()
    return receipt


def _remaining_by_item(db: Session, order: m.PurchaseOrder) -> dict[uuid.UUID, Decimal]:
    ordered = {line.item_id: line.qty for line in _order_lines(db, order.id)}
    received = _received_qty_by_item(db, order.id)
    return {item_id: qty - received.get(item_id, Decimal(0)) for item_id, qty in ordered.items()}


def _build_receipt_line(
    db: Session, receipt: m.Receipt, order: m.PurchaseOrder | None,
    remaining: dict[uuid.UUID, Decimal], line: dict,
) -> m.ReceiptLine:
    item = inv_service.get_item(db, line["item_id"])
    if item.kind == "service":
        raise AccountingError(422, f"service_item_not_receivable: {item.sku}")
    qty = inv_service.quantize4(line["qty"])
    codes = [str(code).strip() for code in line.get("serial_codes") or []]
    if item.tracking == "serial":
        if qty != len(codes):
            raise AccountingError(
                422, f"serial_qty_mismatch: qty={qty} but {len(codes)} codes for {item.sku}"
            )
    if qty <= 0:
        raise AccountingError(422, "qty must be positive")
    if order is not None:
        left = remaining.get(item.id, Decimal(0))
        if qty > left:
            raise AccountingError(
                422, f"over_receipt: {item.sku} ordered left {left}, got {qty}"
            )
        remaining[item.id] = left - qty

    unit_cost = line.get("unit_cost")
    if unit_cost is None and order is not None:
        price = next(
            (ol.unit_price for ol in _order_lines(db, order.id) if ol.item_id == item.id), None
        )
        if price is not None and order.rate is not None:
            # валютная закупка: себестоимость в базовой по замороженному курсу
            unit_cost = quantize2(price * order.rate)
    return m.ReceiptLine(
        receipt_id=receipt.id,
        item_id=item.id,
        location_id=line.get("location_id"),
        qty=qty,
        unit_cost=inv_service.quantize4(unit_cost) if unit_cost is not None else None,
        serial_codes=codes or None,
    )


def _default_location(db: Session, item: inv.Item) -> inv.Location:
    name = DEFAULT_LOCATIONS[item.kind]
    location = db.scalar(select(inv.Location).where(inv.Location.name == name))
    if location is None:
        raise AccountingError(422, f"Default location not found: {name}")
    return location


def post_receipt(db: Session, receipt: m.Receipt) -> m.Receipt:
    """Проведение: движения + серийники + пересчёт средней + статус заказа;
    одна транзакция БД (§2.4)."""
    if receipt.is_stornoed:
        raise AccountingError(422, "Receipt is stornoed and cannot be posted again")
    if receipt.status == "posted":
        raise AccountingError(409, f"Receipt {receipt.number or receipt.id} is already posted")
    transit = db.scalar(select(inv.Location).where(
        inv.Location.name == "Поставщик", inv.Location.is_transit
    ))
    if transit is None:
        raise AccountingError(422, "System transit location not found: Поставщик")

    order = db.get(m.PurchaseOrder, receipt.purchase_order_id) if receipt.purchase_order_id else None
    remaining = _remaining_by_item(db, order) if order else {}
    lines = db.scalars(select(m.ReceiptLine).where(m.ReceiptLine.receipt_id == receipt.id)).all()
    if not lines:
        raise AccountingError(422, "receipt_lines_required")

    amount_base = Decimal(0)
    for line in lines:
        item = inv_service.get_item(db, line.item_id)
        location = (
            inv_service.get_location(db, line.location_id)
            if line.location_id else _default_location(db, item)
        )
        if location.is_transit:
            raise AccountingError(422, "receipt_to_transit_location_is_not_allowed")
        inv_service._check_kind_compat(item, location)
        if item.tracking == "serial" and (line.serial_codes is None or len(line.serial_codes) == 0):
            raise AccountingError(422, f"serial_codes_required: {item.sku}")
        if order is not None:
            left = remaining.get(item.id, Decimal(0))
            if line.qty > left:
                raise AccountingError(422, f"over_receipt: {item.sku} ordered left {left}")
            remaining[item.id] = left - line.qty
        inv_service._apply_receipt(
            db, item=item, to_location=location, qty=line.qty, unit_cost=line.unit_cost,
            user_id=receipt.created_by, from_location=transit,
            counterparty_id=receipt.counterparty_id, source_type="receipt",
            source_id=receipt.id, moved_at=receipt.moved_at,
            note=receipt.counterparty_doc or "", serial_codes=line.serial_codes,
        )
        if line.unit_cost is not None:
            amount_base += quantize2(line.qty * line.unit_cost)

    receipt.status = "posted"
    receipt.number = acc_service.next_doc_number(db, m.RECEIPT_DOC_TYPE, receipt.moved_at)
    receipt.posted_at = datetime.now(UTC)
    db.flush()
    if order is not None:
        _recompute_order_status(db, order)
    events.publish(db, "acc.purchase.received", {
        "receipt_id": str(receipt.id),
        "number": receipt.number,
        "purchase_order_id": str(order.id) if order else None,
        "counterparty_id": str(receipt.counterparty_id),
        "moved_at": receipt.moved_at.isoformat(),
        "amount_base": str(amount_base),
    })
    return receipt


def unpost_receipt(db: Session, receipt: m.Receipt, *, user_id: uuid.UUID, reason: str) -> m.Receipt:
    """Сторно приёмки: парные инверсионные движения «склад → Поставщик»,
    серийники → void. Разрешено только без последующих движений: остатка
    на складе хватает, серийники не выданы/не списаны (§6). Средняя
    себестоимость не откатывается — как у сторно денежных документов."""
    if receipt.status != "posted":
        raise AccountingError(422, "Only posted receipts can be unposted")
    if receipt.is_stornoed:
        raise AccountingError(409, "Receipt is already stornoed")

    moves = db.scalars(select(inv.StockMove).where(
        inv.StockMove.source_type == "receipt", inv.StockMove.source_id == receipt.id
    )).all()
    transit = db.scalar(select(inv.Location).where(
        inv.Location.name == "Поставщик", inv.Location.is_transit
    ))
    for move in moves:
        item = inv_service.get_item(db, move.item_id)
        location = inv_service.get_location(db, move.to_location_id)
        balance = inv_service.location_balance(db, item.id, location.id)
        if balance < move.qty:
            raise AccountingError(
                422,
                f"has_subsequent_moves: {item.sku} on «{location.name}» "
                f"balance {balance} < received {move.qty}",
            )
        serials = db.scalars(select(inv.ItemSerial).where(
            inv.ItemSerial.received_move_id == move.id
        )).all()
        for serial in serials:
            if serial.status != "in_stock":
                raise AccountingError(422, f"has_subsequent_moves: serial {serial.code_hash[:8]}… is not in_stock")

    for move in moves:
        item = db.get(inv.Item, move.item_id)
        location = inv_service.get_location(db, move.to_location_id)
        codes = [
            inv_service.serial_code(serial)
            for serial in db.scalars(select(inv.ItemSerial).where(
                inv.ItemSerial.received_move_id == move.id
            )).all()
        ]
        inv_service._apply_issue(
            db, item=item, from_location=location, qty=move.qty,
            user_id=user_id, to_location=transit, counterparty_id=receipt.counterparty_id,
            source_type="receipt_storno", source_id=receipt.id,
            moved_at=date.today(), note=f"Сторно приёмки {receipt.number}",
            serial_codes=codes, void_serials=True,
        )

    receipt.is_stornoed = True
    db.flush()
    record_version(db, "acc.purchase.receipt", str(receipt.id), user_id,
                   {"is_stornoed": {"old": False, "new": True}}, reason=reason)
    if receipt.purchase_order_id is not None:
        order = db.get(m.PurchaseOrder, receipt.purchase_order_id)
        if order is not None:
            _recompute_order_status(db, order)
    return receipt


# ---------- Оплата ----------

def _purchase_category(db: Session) -> acc.Category:
    """Категория «Закупки товаров» (расход) — авто-seed при первой оплате (§3.3)."""
    category = db.scalar(select(acc.Category).where(
        acc.Category.name == PURCHASE_CATEGORY, acc.Category.kind == "expense"
    ))
    if category is None:
        category = acc.Category(name=PURCHASE_CATEGORY, kind="expense")
        db.add(category)
        db.flush()
    return category


def pay_order(db: Session, *, order: m.PurchaseOrder, user_id: uuid.UUID, data: dict):
    """Исходящая транзакция с категорией «Закупки товаров», контрагентом
    заказа и source-ссылкой в dimensions; частичные оплаты — несколько
    транзакций (§3.3)."""
    if order.status in ("draft", "cancelled"):
        raise AccountingError(422, f"order_not_payable: status={order.status}")
    account = db.get(acc.Account, data["account_id"])
    if account is None or not account.is_active:
        raise AccountingError(422, f"Unknown or inactive account: {data['account_id']}")
    category = _purchase_category(db)
    txn = acc_service.create_transaction(db, user_id=user_id, data={
        "kind": "expense",
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


def _order_payments(db: Session, order_id: uuid.UUID) -> list[acc.Transaction]:
    return db.scalars(
        select(acc.Transaction).where(
            acc.Transaction.status == "posted",
            acc.Transaction.kind == "expense",
            acc.Transaction.dimensions["source_type"].astext == SOURCE_TYPE,
            acc.Transaction.dimensions["source_id"].astext == str(order_id),
        )
    ).all()


# ---------- Отчёты ----------

def purchases_report(
    db: Session, date_from: date, date_to: date, counterparty_id: uuid.UUID | None
) -> dict[str, Any]:
    """Закупки за период: итоги по заказам/приёмкам, по товарам, по поставщикам
    (§3.6). Приёмки — проведённые, сторно исключаются (гасятся); оплаты —
    posted-транзакции с source-ссылкой на заказ."""
    orders = db.scalars(
        select(m.PurchaseOrder).where(
            func.date(m.PurchaseOrder.created_at) >= date_from,
            func.date(m.PurchaseOrder.created_at) <= date_to,
        ).order_by(m.PurchaseOrder.created_at)
    ).all()
    if counterparty_id is not None:
        orders = [o for o in orders if o.counterparty_id == counterparty_id]
    orders_amount = sum((o.amount_base for o in orders), Decimal(0))

    receipts = db.scalars(
        select(m.Receipt).where(
            m.Receipt.status == "posted",
            m.Receipt.is_stornoed.is_(False),
            m.Receipt.moved_at >= date_from,
            m.Receipt.moved_at <= date_to,
        ).order_by(m.Receipt.moved_at)
    ).all()
    if counterparty_id is not None:
        receipts = [r for r in receipts if r.counterparty_id == counterparty_id]

    by_item: dict[uuid.UUID, dict[str, Any]] = {}
    receipts_amount = Decimal(0)
    for receipt in receipts:
        for line in db.scalars(select(m.ReceiptLine).where(
            m.ReceiptLine.receipt_id == receipt.id
        )).all():
            cost = quantize2(line.qty * line.unit_cost) if line.unit_cost else Decimal(0)
            receipts_amount += cost
            bucket = by_item.setdefault(line.item_id, {
                "qty": Decimal(0), "amount_base": Decimal(0),
            })
            bucket["qty"] += line.qty
            bucket["amount_base"] += cost

    by_counterparty: dict[uuid.UUID, dict[str, Any]] = {}
    counterparties = {
        c.id: c for c in db.scalars(select(acc.Counterparty)).all()
    }
    for order in orders:
        bucket = by_counterparty.setdefault(order.counterparty_id, {
            "orders_amount_base": Decimal(0), "received_amount_base": Decimal(0),
            "paid_amount_base": Decimal(0),
        })
        bucket["orders_amount_base"] += order.amount_base
        bucket["paid_amount_base"] += sum(
            (t.amount_base for t in _order_payments(db, order.id)), Decimal(0)
        )
    for receipt in receipts:
        bucket = by_counterparty.setdefault(receipt.counterparty_id, {
            "orders_amount_base": Decimal(0), "received_amount_base": Decimal(0),
            "paid_amount_base": Decimal(0),
        })
        for line in db.scalars(select(m.ReceiptLine).where(
            m.ReceiptLine.receipt_id == receipt.id
        )).all():
            bucket["received_amount_base"] += (
                quantize2(line.qty * line.unit_cost) if line.unit_cost else Decimal(0)
            )

    items = {i.id: i for i in db.scalars(select(inv.Item)).all()}
    return {
        "date_from": date_from,
        "date_to": date_to,
        "counterparty_id": counterparty_id,
        "orders": {"count": len(orders), "amount_base": str(quantize2(orders_amount))},
        "receipts": {"count": len(receipts), "amount_base": str(quantize2(receipts_amount))},
        "by_item": [
            {
                "item_id": str(item_id),
                "sku": items[item_id].sku if item_id in items else "?",
                "item_name": items[item_id].name if item_id in items else "?",
                "qty": str(inv_service.quantize4(bucket["qty"])),
                "amount_base": str(quantize2(bucket["amount_base"])),
            }
            for item_id, bucket in sorted(by_item.items(), key=lambda kv: str(kv[0]))
        ],
        "by_counterparty": [
            {
                "counterparty_id": str(cp_id),
                "name": counterparties[cp_id].name if cp_id in counterparties else "?",
                "orders_amount_base": str(quantize2(bucket["orders_amount_base"])),
                "received_amount_base": str(quantize2(bucket["received_amount_base"])),
                "paid_amount_base": str(quantize2(bucket["paid_amount_base"])),
                "balance": str(quantize2(bucket["received_amount_base"] - bucket["paid_amount_base"])),
            }
            for cp_id, bucket in sorted(by_counterparty.items(), key=lambda kv: str(kv[0]))
        ],
    }


def counterparty_balance(
    db: Session, counterparty_id: uuid.UUID, *, on_date: date | None = None
) -> dict[str, Any]:
    """Взаиморасчёты (§3.6, §11.3 — лёгкие): сальдо = приёмки − оплаты,
    детализация по документам; всё в базовой валюте."""
    _get_counterparty(db, counterparty_id)
    counterparty = db.get(acc.Counterparty, counterparty_id)

    documents: list[dict[str, Any]] = []
    received = Decimal(0)
    receipts = db.scalars(
        select(m.Receipt).where(
            m.Receipt.counterparty_id == counterparty_id,
            m.Receipt.status == "posted",
            m.Receipt.is_stornoed.is_(False),
        ).order_by(m.Receipt.moved_at)
    ).all()
    if on_date is not None:
        receipts = [r for r in receipts if r.moved_at <= on_date]
    for receipt in receipts:
        amount = sum((
            quantize2(line.qty * line.unit_cost)
            for line in db.scalars(select(m.ReceiptLine).where(
                m.ReceiptLine.receipt_id == receipt.id
            )).all() if line.unit_cost
        ), Decimal(0))
        received += amount
        documents.append({
            "date": receipt.moved_at.isoformat(),
            "kind": "receipt",
            "number": receipt.number,
            "amount_base": str(amount),
        })

    paid = Decimal(0)
    payments = db.scalars(
        select(acc.Transaction).where(
            acc.Transaction.status == "posted",
            acc.Transaction.kind == "expense",
            acc.Transaction.counterparty_id == counterparty_id,
            acc.Transaction.dimensions["source_type"].astext == SOURCE_TYPE,
        ).order_by(acc.Transaction.operated_at)
    ).all()
    if on_date is not None:
        payments = [t for t in payments if t.operated_at <= on_date]
    for txn in payments:
        paid += txn.amount_base
        documents.append({
            "date": txn.operated_at.isoformat(),
            "kind": "payment",
            "number": txn.doc_number,
            "amount_base": str(txn.amount_base),
        })

    return {
        "counterparty_id": str(counterparty_id),
        "name": counterparty.name,
        "on_date": on_date.isoformat() if on_date else None,
        "received_amount_base": str(quantize2(received)),
        "paid_amount_base": str(quantize2(paid)),
        # положительное сальдо — задолженность перед поставщиком
        "balance": str(quantize2(received - paid)),
        "documents": sorted(documents, key=lambda d: d["date"]),
    }
