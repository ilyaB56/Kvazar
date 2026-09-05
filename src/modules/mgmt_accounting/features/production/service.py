"""Сервисный слой фичи production: тех.карты и заказы на сборку.

Инварианты (resources-core §3.5, §4):
- тех.карта — одноуровневый BOM: продукция (не услуга, не серийная —
  поэкземплярный учёт в сборке v1 не поддерживается) и компоненты
  (количественные, серийные компоненты — 422);
- проведение — одна транзакция (§2.4): списание компонентов
  «Склад → Производство» по текущей средней с контролем ДОСТУПНОГО
  остатка (баланс − резервы заказов продаж, резерв v1), оприходование
  продукции «Производство → Склад» по себестоимости материалов
  (unit_cost = Σ материалов / выпуск), пересчёт avg продукции;
- сторно (unpost) — по правилам сторно приёмок: парные движения
  (продукция «Склад → Производство», компоненты «Производство → Склад»),
  только без последующих движений по продукции; средняя не откатывается.

Событие: acc.production.order.posted (количества/деньги строками).
Частичное выполнение и трудозатраты — v2 (§9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core import events
from src.core.versioning import record_version
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service
from src.modules.mgmt_accounting.features.production import models as m
from src.modules.mgmt_accounting.features.sales import service as sales_service
from src.modules.mgmt_accounting.service import AccountingError, quantize2

DEFAULT_LOCATIONS = {"physical": "Основной склад", "digital": "Цифровой склад"}
TRANSIT_PRODUCTION = "Производство"


def _default_location(db: Session, item: inv.Item) -> inv.Location:
    name = DEFAULT_LOCATIONS[item.kind]
    location = db.scalar(select(inv.Location).where(inv.Location.name == name))
    if location is None:
        raise AccountingError(422, f"Default location not found: {name}")
    return location


def _production_transit(db: Session) -> inv.Location:
    location = db.scalar(select(inv.Location).where(
        inv.Location.name == TRANSIT_PRODUCTION, inv.Location.is_transit
    ))
    if location is None:
        raise AccountingError(422, f"System transit location not found: {TRANSIT_PRODUCTION}")
    return location


# ---------- Тех.карты ----------

def create_tech_card(db: Session, *, user_id: uuid.UUID, data: dict) -> m.TechCard:
    product = inv_service.get_item(db, data["product_item_id"])
    if product.kind == "service":
        raise AccountingError(422, f"product_is_service: {product.sku}")
    if product.tracking == "serial":
        raise AccountingError(
            422, f"product_is_serial: {product.sku} — поэкземплярная сборка не поддерживается (v1)"
        )
    qty_out = inv_service.quantize4(data["qty_out"])
    if qty_out <= 0:
        raise AccountingError(422, "qty_out must be positive")
    components = []
    for row in data.get("components") or []:
        component = inv_service.get_item(db, row["item_id"])
        qty = inv_service.quantize4(row["qty"])
        if qty <= 0:
            raise AccountingError(422, f"component qty must be positive: {component.sku}")
        if component.kind == "service":
            raise AccountingError(422, f"component_is_service: {component.sku}")
        if component.tracking == "serial":
            raise AccountingError(
                422, f"component_is_serial: {component.sku} — серийные компоненты не поддерживаются (v1)"
            )
        if component.id == product.id:
            raise AccountingError(422, "component_equals_product")
        components.append({"item_id": str(component.id), "qty": str(qty)})
    if not components:
        raise AccountingError(422, "components_required")
    card = m.TechCard(
        name=data["name"],
        product_item_id=product.id,
        qty_out=qty_out,
        components=components,
        created_by=user_id,
    )
    db.add(card)
    db.flush()
    return card


def card_components(db: Session, card: m.TechCard) -> list[tuple[inv.Item, Decimal]]:
    return [
        (inv_service.get_item(db, uuid.UUID(row["item_id"])), Decimal(row["qty"]))
        for row in card.components
    ]


# ---------- Заказы на сборку ----------

def create_order(db: Session, *, user_id: uuid.UUID, data: dict) -> m.ProductionOrder:
    card = db.get(m.TechCard, data["tech_card_id"])
    if card is None or not card.is_active:
        raise AccountingError(422, f"Unknown or inactive tech card: {data['tech_card_id']}")
    qty_planned = inv_service.quantize4(data["qty_planned"])
    if qty_planned <= 0:
        raise AccountingError(422, "qty_planned must be positive")
    order = m.ProductionOrder(
        tech_card_id=card.id,
        qty_planned=qty_planned,
        status="draft",
        moved_at=data.get("moved_at") or date.today(),
        note=data.get("note", ""),
        created_by=user_id,
    )
    db.add(order)
    db.flush()
    return order


def post_order(db: Session, order: m.ProductionOrder) -> m.ProductionOrder:
    """Проведение одной транзакцией: списание компонентов по средней
    (доступный остаток = баланс − резервы продаж), оприходование продукции
    по себестоимости материалов (§3.5, §4)."""
    if order.is_stornoed:
        raise AccountingError(422, "Order is stornoed and cannot be posted again")
    if order.status != "draft":
        raise AccountingError(409, f"Only draft orders can be posted (status={order.status})")
    card = db.get(m.TechCard, order.tech_card_id)
    if card is None or not card.is_active:
        raise AccountingError(422, "tech_card_inactive")
    product = inv_service.get_item(db, card.product_item_id)
    transit = _production_transit(db)
    product_location = _default_location(db, product)

    material_cost = Decimal(0)
    for component, per_card_qty in card_components(db, card):
        location = _default_location(db, component)
        qty = per_card_qty * order.qty_planned
        # доступный остаток = физический − резервы заказов продаж (v1)
        balance = inv_service.location_balance(db, component.id, location.id)
        reserved = sales_service.reserved_qty_by_item(db, component.id)
        if balance - reserved < qty:
            raise AccountingError(
                422,
                f"insufficient_stock: {component.sku} on «{location.name}» "
                f"available {balance - reserved} (balance {balance}, reserved {reserved})",
            )
        move = inv_service._apply_issue(
            db, item=component, from_location=location, qty=qty,
            user_id=order.created_by, to_location=transit,
            counterparty_id=None, source_type="production", source_id=order.id,
            moved_at=order.moved_at, note=order.note or f"Сборка {card.name}",
            serial_codes=None,
        )
        if move.unit_cost is not None:
            material_cost += quantize2(move.qty * move.unit_cost)

    produced = inv_service.quantize4(card.qty_out * order.qty_planned)
    unit_cost = inv_service.quantize4(material_cost / produced) if produced else Decimal(0)
    inv_service._apply_receipt(
        db, item=product, to_location=product_location, qty=produced,
        unit_cost=unit_cost, user_id=order.created_by, from_location=transit,
        counterparty_id=None, source_type="production", source_id=order.id,
        moved_at=order.moved_at, note=order.note or f"Сборка {card.name}",
        serial_codes=None,
    )

    order.status = "posted"
    order.number = _next_number(db, order.moved_at)
    order.material_cost = inv_service.quantize4(material_cost)
    order.posted_at = datetime.now(UTC)
    db.flush()
    events.publish(db, "acc.production.order.posted", {
        "order_id": str(order.id),
        "number": order.number,
        "tech_card_id": str(card.id),
        "product_item_id": str(product.id),
        "produced_qty": str(produced),
        "material_cost": str(order.material_cost),
        "unit_cost": str(unit_cost),
    })
    return order


def _next_number(db: Session, moved_at: date) -> str:
    from src.modules.mgmt_accounting import service as acc_service

    return acc_service.next_doc_number(db, m.ORDER_DOC_TYPE, moved_at)


def cancel_order(db: Session, order: m.ProductionOrder, *, user_id: uuid.UUID) -> m.ProductionOrder:
    if order.status != "draft":
        raise AccountingError(409, f"Only draft orders can be cancelled (status={order.status})")
    order.status = "cancelled"
    db.flush()
    record_version(db, "acc.production.order", str(order.id), user_id,
                   {"status": {"old": "draft", "new": "cancelled"}})
    return order


def unpost_order(db: Session, order: m.ProductionOrder, *, user_id: uuid.UUID, reason: str) -> m.ProductionOrder:
    """Сторно сборки по правилам сторно приёмок: парные движения
    (продукция «Склад → Производство», компоненты «Производство → Склад»),
    только без последующих движений по продукции; средняя не откатывается."""
    if order.status != "posted":
        raise AccountingError(422, "Only posted orders can be unposted")
    if order.is_stornoed:
        raise AccountingError(409, "Order is already stornoed")
    card = db.get(m.TechCard, order.tech_card_id)
    product = inv_service.get_item(db, card.product_item_id)
    transit = _production_transit(db)
    product_location = _default_location(db, product)

    # продукция должна всё ещё лежать на складе — иначе последующие движения
    produced = inv_service.quantize4(card.qty_out * order.qty_planned)
    balance = inv_service.location_balance(db, product.id, product_location.id)
    if balance < produced:
        raise AccountingError(
            422,
            f"has_subsequent_moves: {product.sku} balance {balance} < produced {produced}",
        )

    inv_service._apply_issue(
        db, item=product, from_location=product_location, qty=produced,
        user_id=user_id, to_location=transit, counterparty_id=None,
        source_type="production_storno", source_id=order.id,
        moved_at=date.today(), note=f"Сторно сборки {order.number}",
        serial_codes=None,
    )
    for component, per_card_qty in card_components(db, card):
        location = _default_location(db, component)
        inv_service._apply_receipt(
            db, item=component, to_location=location,
            qty=per_card_qty * order.qty_planned,
            unit_cost=None,  # средняя не откатывается (правила сторно)
            user_id=user_id, from_location=transit, counterparty_id=None,
            source_type="production_storno", source_id=order.id,
            moved_at=date.today(), note=f"Сторно сборки {order.number}",
            serial_codes=None,
        )

    order.is_stornoed = True
    db.flush()
    record_version(db, "acc.production.order", str(order.id), user_id,
                   {"is_stornoed": {"old": False, "new": True}}, reason=reason)
    return order


def order_payload(order: m.ProductionOrder) -> dict[str, Any]:
    return {
        "order_id": str(order.id),
        "number": order.number,
        "tech_card_id": str(order.tech_card_id),
        "qty_planned": str(order.qty_planned),
        "status": order.status,
        "is_stornoed": order.is_stornoed,
        "material_cost": str(order.material_cost) if order.material_cost is not None else None,
    }
