"""API фичи sales (resources-core §3.4, §6): заказы клиентов, отгрузки, оплаты.

Зеркально закупкам; GET — CurrentUser, мутации — WriteUser (§8).
Деньги/количества в ответах — строками (ADR-003).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core import events
from src.core.auth import CurrentUser, WriteUser, module_level
from src.core.models import AuditEvent
from src.core.versioning import record_version
from src.db import get_db
from src.modules.mgmt_accounting.features.sales import models as m
from src.modules.mgmt_accounting.features.sales import service
from src.modules.mgmt_accounting.service import AccountingError
from src.modules.mgmt_accounting.router import TransactionOut
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service

router = APIRouter(tags=["sales"])

MoneyStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, Decimal) else v)]


@contextmanager
def svc():
    """Перевод бизнес-ошибок сервисного слоя в HTTP-ответы."""
    try:
        yield
    except AccountingError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------- Schemas ----------

class OrderLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class OrderIn(BaseModel):
    # counterparty_id не нужен, если передан crm_deal_id — префилл из сделки
    model_config = {"json_schema_extra": {"example": {
        "counterparty_id": "uuid-клиент", "crm_deal_id": "uuid-сделки-опционально",
        "currency": "RUB",
        "lines": [{"item_id": "uuid", "qty": "2", "unit_price": "300"}],
    }}}

    counterparty_id: uuid.UUID | None = None  # или префилл из crm_deal_id
    crm_deal_id: uuid.UUID | None = None
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    note: str = ""
    lines: list[OrderLineIn] = Field(min_length=1)


class OrderLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    qty: MoneyStr
    unit_price: MoneyStr
    amount: MoneyStr
    reserved_qty: MoneyStr

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str | None
    counterparty_id: uuid.UUID
    crm_deal_id: uuid.UUID | None
    status: str
    currency: str
    rate: MoneyStr | None
    amount: MoneyStr
    amount_base: MoneyStr
    note: str
    created_at: datetime
    lines: list[OrderLineOut] = []

    model_config = {"from_attributes": True}


class ShipmentLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    location_id: uuid.UUID | None = None
    serial_codes: list[str] | None = None  # пусто → FIFO-автовыбор при post


class ShipmentIn(BaseModel):
    # serial_codes: пусто → FIFO-автовыбор при проведении; иначе полный список
    model_config = {"json_schema_extra": {"example": {
        "sales_order_id": "uuid-заказа",
        "lines": [{"item_id": "uuid", "qty": "2"}],
    }}}

    sales_order_id: uuid.UUID
    counterparty_doc: str | None = Field(default=None, max_length=60)
    moved_at: date | None = None
    note: str = ""
    lines: list[ShipmentLineIn] = Field(min_length=1)


class ShipmentLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    location_id: uuid.UUID | None
    qty: MoneyStr
    unit_price: MoneyStr | None
    amount_base: MoneyStr | None
    serial_ids: list[str] | None
    # коды-активы: расшифровка только rw (require_module accounting rw);
    # readonly видит отпечатки (первые 8 hex code_hash) — факт без актива
    serial_codes: list[str] | None = None
    serial_fingerprints: list[str] | None = None

    model_config = {"from_attributes": True}


class ShipmentOut(BaseModel):
    id: uuid.UUID
    number: str | None
    sales_order_id: uuid.UUID
    status: str
    is_stornoed: bool
    counterparty_doc: str | None
    note: str
    moved_at: date
    created_at: datetime
    lines: list[ShipmentLineOut] = []

    model_config = {"from_attributes": True}


class PayIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "account_id": "uuid-счёта", "amount": "400",
    }}}

    account_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    operated_at: date | None = None
    description: str = ""


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1)


# ---------- Заказы ----------

def _order_with_lines(db: Session, order: m.SalesOrder) -> OrderOut:
    lines = db.scalars(
        select(m.SalesOrderLine).where(m.SalesOrderLine.order_id == order.id)
    ).all()
    return OrderOut.model_validate(order).model_copy(
        update={"lines": [OrderLineOut.model_validate(line) for line in lines]}
    )


def _get_order(db: Session, order_id: uuid.UUID) -> m.SalesOrder:
    order = db.get(m.SalesOrder, order_id)
    if order is None:
        raise HTTPException(404, "Sales order not found")
    return order


@router.get("/sales-orders", response_model=list[OrderOut])
def list_orders(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    counterparty_id: uuid.UUID | None = None,
    crm_deal_id: uuid.UUID | None = None,
):
    query = select(m.SalesOrder).order_by(m.SalesOrder.created_at.desc())
    if status is not None:
        query = query.where(m.SalesOrder.status == status)
    if counterparty_id is not None:
        query = query.where(m.SalesOrder.counterparty_id == counterparty_id)
    if crm_deal_id is not None:
        query = query.where(m.SalesOrder.crm_deal_id == crm_deal_id)
    return [_order_with_lines(db, order) for order in db.scalars(query).all()]


@router.post("/sales-orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderIn, user: WriteUser, db: Session = Depends(get_db)):
    with svc():
        order = service.create_order(db, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(order)  # единый формат сумм с GET: значения из БД
    return _order_with_lines(db, order)


@router.get("/sales-orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)):
    return _order_with_lines(db, _get_order(db, order_id))


@router.post("/sales-orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    with svc():
        service.confirm_order(db, order, user_id=user.id)
    db.commit()
    db.refresh(order)
    return _order_with_lines(db, order)


@router.post("/sales-orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    with svc():
        service.cancel_order(db, order, user_id=user.id)
    db.commit()
    db.refresh(order)
    return _order_with_lines(db, order)


@router.post("/sales-orders/{order_id}/pay", response_model=TransactionOut)
def pay_order(order_id: uuid.UUID, body: PayIn, user: WriteUser, db: Session = Depends(get_db)):
    """Оплата заказа: входящая транзакция, категория «Продажи» (авто-seed),
    контрагент наследуется; частичные — несколько оплат (§3.4)."""
    order = _get_order(db, order_id)
    with svc():
        txn = service.pay_order(db, order=order, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(txn)
    return txn


# ---------- Отгрузки ----------

def _shipment_with_lines(db: Session, shipment: m.Shipment,
                         reveal_codes: bool = True) -> ShipmentOut:
    """Строки отгрузки: rw получает расшифрованные коды, ro — отпечатки."""
    lines = db.scalars(
        select(m.ShipmentLine).where(m.ShipmentLine.shipment_id == shipment.id)
    ).all()
    out_lines: list[ShipmentLineOut] = []
    for line in lines:
        out = ShipmentLineOut.model_validate(line)
        out.serial_codes = None
        out.serial_fingerprints = None
        if line.serial_ids:
            serials = db.scalars(select(inv.ItemSerial).where(
                inv.ItemSerial.id.in_([uuid.UUID(str(i)) for i in line.serial_ids]))).all()
            by_id = {str(serial.id): serial for serial in serials}
            ordered = [by_id[str(i)] for i in line.serial_ids if str(i) in by_id]
            if reveal_codes:
                out.serial_codes = [inv_service.serial_code(serial) for serial in ordered]
            else:
                out.serial_fingerprints = [serial.code_hash[:8] for serial in ordered]
        out_lines.append(out)
    return ShipmentOut.model_validate(shipment).model_copy(update={"lines": out_lines})


def _get_shipment(db: Session, shipment_id: uuid.UUID) -> m.Shipment:
    shipment = db.get(m.Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Shipment not found")
    return shipment


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    sales_order_id: uuid.UUID | None = None,
):
    reveal = module_level(db, user.role, "accounting") == "rw"
    query = select(m.Shipment).order_by(m.Shipment.created_at.desc())
    if status is not None:
        query = query.where(m.Shipment.status == status)
    if sales_order_id is not None:
        query = query.where(m.Shipment.sales_order_id == sales_order_id)
    return [_shipment_with_lines(db, shipment, reveal) for shipment in db.scalars(query).all()]


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentIn, user: WriteUser, db: Session = Depends(get_db)):
    with svc():
        shipment = service.create_shipment(db, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(shipment)  # единый формат сумм с GET: значения из БД
    return _shipment_with_lines(db, shipment)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def get_shipment(shipment_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)):
    reveal = module_level(db, user.role, "accounting") == "rw"
    return _shipment_with_lines(db, _get_shipment(db, shipment_id), reveal)


@router.post("/shipments/{shipment_id}/post", response_model=ShipmentOut)
def post_shipment(shipment_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    shipment = _get_shipment(db, shipment_id)
    with svc():
        service.post_shipment(db, shipment)
    db.commit()
    db.refresh(shipment)
    return _shipment_with_lines(db, shipment)


class DeliverIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {"channel_note": "email"}}}

    channel_note: str = Field(default="", max_length=60)


@router.post("/shipments/{shipment_id}/deliver")
def deliver_shipment(shipment_id: uuid.UUID, body: DeliverIn | None = None,
                     user: WriteUser = None, db: Session = Depends(get_db)):
    """Разовая выдача расшифрованных кодов отгрузки (sales-automation §5.2).

    Возвращает serials один раз; помечает delivered_at/delivered_via, пишет
    record_versions/аудит и событие acc.shipment.delivered (без кодов).
    Повторный вызов — 409 с фактом доставки. Права: rw (служебный токен
    роли user — rw)."""
    from datetime import UTC, datetime

    shipment = db.get(m.Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Shipment not found")
    if shipment.status != "posted":
        raise HTTPException(422, "Only posted shipments can be delivered")
    if shipment.is_stornoed:
        raise HTTPException(422, "Shipment is stornoed")
    if shipment.delivered_at is not None:
        raise HTTPException(
            409,
            f"Shipment {shipment.number} already delivered "
            f"at {shipment.delivered_at.isoformat()} via {shipment.delivered_via or '—'}",
        )

    channel = (body.channel_note if body and body.channel_note else "manual").strip().lower()
    serials_out: list[dict] = []
    lines = db.scalars(select(m.ShipmentLine).where(
        m.ShipmentLine.shipment_id == shipment.id)).all()
    for line in lines:
        item = db.get(inv.Item, line.item_id)
        if item is None or item.tracking != "serial" or not line.serial_ids:
            continue
        rows = db.scalars(select(inv.ItemSerial).where(
            inv.ItemSerial.id.in_([uuid.UUID(str(i)) for i in line.serial_ids]))).all()
        serials_out.append({
            "item_id": str(line.item_id),
            "sku": item.sku,
            "codes": [inv_service.serial_code(row) for row in rows],
        })

    shipment.delivered_at = datetime.now(UTC)
    shipment.delivered_via = channel[:12]
    db.add(AuditEvent(
        user_id=user.id if user else None,
        action="shipment.delivered", entity_type="shipment",
        entity_id=str(shipment.id),
        payload={"channel": channel, "serial_count": sum(len(x["codes"]) for x in serials_out)},
    ))
    record_version(db, "acc.sales.shipment", str(shipment.id),
                   user.id if user else None,
                   {"delivered": {"new": True, "channel": channel}})
    events.publish(db, "acc.shipment.delivered", {
        "shipment_id": str(shipment.id),
        "sales_order_id": str(shipment.sales_order_id),
        "channel": channel,
    })
    db.commit()
    events.dispatch_outbox(db)
    return {"delivered_at": shipment.delivered_at.isoformat(),
            "channel": channel, "serials": serials_out}


@router.post("/shipments/{shipment_id}/unpost", response_model=ShipmentOut)
def unpost_shipment(
    shipment_id: uuid.UUID, body: ReasonIn, user: WriteUser, db: Session = Depends(get_db)
):
    """Сторно отгрузки: инверсионные движения «Клиент → склад» (§6)."""
    shipment = _get_shipment(db, shipment_id)
    with svc():
        service.unpost_shipment(db, shipment, user_id=user.id, reason=body.reason)
    db.commit()
    db.refresh(shipment)
    return _shipment_with_lines(db, shipment)


# ---------- Отчёты ----------

@router.get("/reports/sales")
def sales_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
    counterparty_id: uuid.UUID | None = None,
):
    from datetime import timedelta

    date_to = date_to or date.today()
    date_from = date_from or (date_to - timedelta(days=365))
    with svc():
        return service.sales_report(db, date_from, date_to, counterparty_id)
