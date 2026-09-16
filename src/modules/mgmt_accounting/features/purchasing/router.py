"""API фичи purchasing (resources-core §3.3, §6): заказы, приёмки, оплаты.

Весь — под авторизацией; GET — CurrentUser, мутации — WriteUser (§8).
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.auth import CurrentUser, WriteUser
from src.core.auth import CompanyScoped
from src.db import get_db
from src.modules.mgmt_accounting import models as acc
from src.modules.mgmt_accounting.features.purchasing import models as m
from src.modules.mgmt_accounting.features.purchasing import service
from src.modules.mgmt_accounting.router import TransactionOut
from src.modules.mgmt_accounting.service import AccountingError

from src.core.pagination import Page, PageParams, page_params
router = APIRouter(tags=["purchasing"])
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
    model_config = {"json_schema_extra": {"example": {
        "counterparty_id": "uuid-поставщик", "currency": "RUB",
        "lines": [{"item_id": "uuid", "qty": "10", "unit_price": "125"}],
    }}}

    counterparty_id: uuid.UUID
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    note: str = ""
    lines: list[OrderLineIn] = Field(min_length=1)


class OrderLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    qty: MoneyStr
    unit_price: MoneyStr
    amount: MoneyStr

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str | None
    counterparty_id: uuid.UUID
    status: str
    currency: str
    rate: MoneyStr | None
    amount: MoneyStr
    amount_base: MoneyStr
    note: str
    created_at: datetime
    lines: list[OrderLineOut] = []
    counterparty_name: str | None = None

    model_config = {"from_attributes": True}


class ReceiptLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)  # базовая валюта
    location_id: uuid.UUID | None = None
    serial_codes: list[str] | None = None


class ReceiptIn(BaseModel):
    # unit_cost — в базовой валюте; для строки заказа по умолчанию
    # цена × замороженный курс заказа
    model_config = {"json_schema_extra": {"example": {
        "purchase_order_id": "uuid-заказа",
        "counterparty_doc": "накладная №45",
        "lines": [
            {"item_id": "uuid", "qty": "4", "unit_cost": "125"},
            {"item_id": "uuid-цифровой", "qty": "2", "serial_codes": ["CODE-1", "CODE-2"]},
        ],
    }}}

    purchase_order_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None  # наследуется из заказа
    counterparty_doc: str | None = Field(default=None, max_length=60)
    moved_at: date | None = None
    note: str = ""
    lines: list[ReceiptLineIn] = Field(min_length=1)


class ReceiptLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    location_id: uuid.UUID | None
    qty: MoneyStr
    unit_cost: MoneyStr | None
    serial_codes: list[str] | None

    model_config = {"from_attributes": True}


class ReceiptOut(BaseModel):
    id: uuid.UUID
    number: str | None
    purchase_order_id: uuid.UUID | None
    counterparty_id: uuid.UUID
    status: str
    is_stornoed: bool
    counterparty_doc: str | None
    note: str
    moved_at: date
    created_at: datetime
    lines: list[ReceiptLineOut] = []
    counterparty_name: str | None = None
    purchase_order_number: str | None = None

    model_config = {"from_attributes": True}


class PayIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "account_id": "uuid-счёта", "amount": "400",
        "description": "частичная оплата",
    }}}

    account_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    operated_at: date | None = None
    description: str = ""


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1)


# ---------- Заказы ----------

def _cp_names(db: Session, ids: set) -> dict:
    """id контрагента -> имя (батч)."""
    if not ids:
        return {}
    return {row[0]: row[1] for row in db.execute(
        select(acc.Counterparty.id, acc.Counterparty.name)
        .where(acc.Counterparty.id.in_(ids))
    ).all()}

def _orders_with_lines(db: Session, orders: list) -> list[OrderOut]:
    names = _cp_names(db, {o.counterparty_id for o in orders})
    return [_order_with_lines(db, o).model_copy(
        update={"counterparty_name": names.get(o.counterparty_id)}) for o in orders]


def _order_with_lines(db: Session, order: m.PurchaseOrder) -> OrderOut:
    lines = db.scalars(
        select(m.PurchaseOrderLine).where(m.PurchaseOrderLine.order_id == order.id)
    ).all()
    return OrderOut.model_validate(order).model_copy(
        update={"lines": [OrderLineOut.model_validate(line) for line in lines]}
    )


def _get_order(db: Session, order_id: uuid.UUID,
              scoped: uuid.UUID | None = None) -> m.PurchaseOrder:
    order = db.get(m.PurchaseOrder, order_id)
    if order is None or (scoped is not None and order.company_id != scoped):
        raise HTTPException(404, "Purchase order not found")
    return order


@router.get("/purchase-orders", response_model=list[OrderOut] | Page[OrderOut])
def list_orders(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    counterparty_id: uuid.UUID | None = None,
    scoped: CompanyScoped = None,
    page: PageParams = Depends(page_params),
):
    query = select(m.PurchaseOrder).where(
        m.PurchaseOrder.company_id == scoped).order_by(m.PurchaseOrder.created_at.desc())
    if status is not None:
        query = query.where(m.PurchaseOrder.status == status)
    if counterparty_id is not None:
        query = query.where(m.PurchaseOrder.counterparty_id == counterparty_id)
    if page.paginated:
        total = db.scalar(select(func.count()).select_from(
            query.order_by(None).subquery())) or 0
        paged = query.offset(page.offset or 0)
        if page.limit:
            paged = paged.limit(page.limit)
        return {"items": _orders_with_lines(db, db.scalars(paged).all()),
                "total": int(total)}
    return _orders_with_lines(db, db.scalars(query).all())


@router.post("/purchase-orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderIn, user: WriteUser, db: Session = Depends(get_db),
                scoped: CompanyScoped = None):
    with svc():
        order = service.create_order(db, user_id=user.id, data=body.model_dump(),
                                     company_id=scoped)
    db.commit()
    db.refresh(order)  # единый формат сумм с GET: значения из БД
    return _order_with_lines(db, order)


@router.get("/purchase-orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db),
             scoped: CompanyScoped = None):
    return _order_with_lines(db, _get_order(db, order_id, scoped))


@router.post("/purchase-orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db),
                 scoped: CompanyScoped = None):
    order = _get_order(db, order_id, scoped)
    with svc():
        service.confirm_order(db, order, user_id=user.id)
    db.commit()
    db.refresh(order)
    return _order_with_lines(db, order)


@router.post("/purchase-orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db),
                scoped: CompanyScoped = None):
    order = _get_order(db, order_id, scoped)
    with svc():
        service.cancel_order(db, order, user_id=user.id)
    db.commit()
    db.refresh(order)
    return _order_with_lines(db, order)


@router.post("/purchase-orders/{order_id}/pay", response_model=TransactionOut)
def pay_order(order_id: uuid.UUID, body: PayIn, user: WriteUser,
              db: Session = Depends(get_db), scoped: CompanyScoped = None):
    """Оплата заказа: исходящая транзакция, категория «Закупки товаров»
    (авто-seed), контрагент наследуется; частичные — несколько оплат (§3.3)."""
    order = _get_order(db, order_id, scoped)
    with svc():
        txn = service.pay_order(db, order=order, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(txn)
    return txn


# ---------- Приёмки ----------

def _receipts_with_lines(db: Session, receipts: list) -> list[ReceiptOut]:
    names = _cp_names(db, {r.counterparty_id for r in receipts})
    order_ids = {r.purchase_order_id for r in receipts if r.purchase_order_id}
    numbers = {row[0]: row[1] for row in db.execute(
        select(m.PurchaseOrder.id, m.PurchaseOrder.number)
        .where(m.PurchaseOrder.id.in_(order_ids))
    ).all()} if order_ids else {}
    out = []
    for r in receipts:
        item = _receipt_with_lines(db, r).model_copy(update={
            "counterparty_name": names.get(r.counterparty_id),
            "purchase_order_number": numbers.get(r.purchase_order_id),
        })
        out.append(item)
    return out


def _receipt_with_lines(db: Session, receipt: m.Receipt) -> ReceiptOut:
    lines = db.scalars(
        select(m.ReceiptLine).where(m.ReceiptLine.receipt_id == receipt.id)
    ).all()
    return ReceiptOut.model_validate(receipt).model_copy(
        update={"lines": [ReceiptLineOut.model_validate(line) for line in lines]}
    )


def _get_receipt(db: Session, receipt_id: uuid.UUID,
                scoped: uuid.UUID | None = None) -> m.Receipt:
    receipt = db.get(m.Receipt, receipt_id)
    if receipt is None or (scoped is not None and receipt.company_id != scoped):
        raise HTTPException(404, "Receipt not found")
    return receipt


@router.get("/receipts", response_model=list[ReceiptOut] | Page[ReceiptOut])
def list_receipts(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    purchase_order_id: uuid.UUID | None = None,
    scoped: CompanyScoped = None,
    page: PageParams = Depends(page_params),
):
    query = select(m.Receipt).where(
        m.Receipt.company_id == scoped).order_by(m.Receipt.created_at.desc())
    if status is not None:
        query = query.where(m.Receipt.status == status)
    if purchase_order_id is not None:
        query = query.where(m.Receipt.purchase_order_id == purchase_order_id)
    if page.paginated:
        total = db.scalar(select(func.count()).select_from(
            query.order_by(None).subquery())) or 0
        paged = query.offset(page.offset or 0)
        if page.limit:
            paged = paged.limit(page.limit)
        return {"items": _receipts_with_lines(db, db.scalars(paged).all()),
                "total": int(total)}
    return [
        _receipt_with_lines(db, receipt) for receipt in db.scalars(query).all()
    ]


@router.post("/receipts", response_model=ReceiptOut, status_code=201)
def create_receipt(body: ReceiptIn, user: WriteUser, db: Session = Depends(get_db),
                  scoped: CompanyScoped = None):
    with svc():
        receipt = service.create_receipt(db, user_id=user.id, data=body.model_dump(),
                                         company_id=scoped)
    db.commit()
    db.refresh(receipt)  # единый формат сумм с GET: значения из БД
    return _receipt_with_lines(db, receipt)


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db),
                scoped: CompanyScoped = None):
    return _receipt_with_lines(db, _get_receipt(db, receipt_id, scoped))


@router.post("/receipts/{receipt_id}/post", response_model=ReceiptOut)
def post_receipt(receipt_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db),
                 scoped: CompanyScoped = None):
    receipt = _get_receipt(db, receipt_id, scoped)
    with svc():
        service.post_receipt(db, receipt)
    db.commit()
    db.refresh(receipt)
    return _receipt_with_lines(db, receipt)


@router.post("/receipts/{receipt_id}/unpost", response_model=ReceiptOut)
def unpost_receipt(
    receipt_id: uuid.UUID, body: ReasonIn, user: WriteUser, db: Session = Depends(get_db),
    scoped: CompanyScoped = None,
):
    """Сторно приёмки: инверсионные движения, только без последующих (§6)."""
    receipt = _get_receipt(db, receipt_id, scoped)
    with svc():
        service.unpost_receipt(db, receipt, user_id=user.id, reason=body.reason)
    db.commit()
    db.refresh(receipt)
    return _receipt_with_lines(db, receipt)


# ---------- Отчёты ----------

@router.get("/reports/purchases")
def purchases_report(
    user: CurrentUser,
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
    counterparty_id: uuid.UUID | None = None,
    scoped: CompanyScoped = None,
):
    from datetime import timedelta

    date_to = date_to or date.today()
    date_from = date_from or (date_to - timedelta(days=365))
    with svc():
        return service.purchases_report(db, date_from, date_to, counterparty_id,
                                        company_id=scoped)


@router.get("/reports/counterparty-balance")
def counterparty_balance(
    user: CurrentUser,
    db: Session = Depends(get_db),
    counterparty_id: uuid.UUID = None,
    on_date: date | None = None,
    scoped: CompanyScoped = None,
):
    if counterparty_id is None:
        raise HTTPException(422, "counterparty_id is required")
    with svc():
        return service.counterparty_balance(db, counterparty_id, on_date=on_date,
                                            company_id=scoped)
