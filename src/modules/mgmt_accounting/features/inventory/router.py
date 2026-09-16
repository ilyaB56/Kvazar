"""API фичи inventory (resources-core, этап A): НСИ + склад-ядро.

Весь — под авторизацией, матрица ролей модуля действует как есть (§8):
GET — CurrentUser, мутации — WriteUser.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.core.auth import CurrentUser, WriteUser, require_module
from src.core.auth import CompanyScoped
from src.core.pagination import Page, PageParams, page_params
from src.core.models import User
from src.db import get_db
from src.modules.mgmt_accounting.features.inventory import models as m
from src.modules.mgmt_accounting.features.inventory import service
from src.modules.mgmt_accounting.service import AccountingError

router = APIRouter(tags=["inventory"])

# Количества/деньги в ответах — строками (Decimal без float, ADR-003)
MoneyStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, Decimal) else v)]


@contextmanager
def svc():
    """Перевод бизнес-ошибок сервисного слоя в HTTP-ответы."""
    try:
        yield
    except AccountingError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------- Schemas: НСИ ----------

class ItemIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "sku": "WIDGET-01", "name": "Виджет алюминиевый", "kind": "physical",
        "unit_code": "шт", "barcode": "4600000000000",
        "sale_price": "1500.00", "low_stock_threshold": "5",
    }}}

    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(pattern=r"^(physical|digital|service)$")
    unit_code: str
    tracking: str | None = Field(default=None, pattern=r"^(qty|serial)$")
    barcode: str | None = None
    sale_price: Decimal | None = Field(default=None, ge=0)
    low_stock_threshold: Decimal | None = Field(default=None, ge=0)


class ItemPatch(BaseModel):
    name: str | None = None
    barcode: str | None = None
    sale_price: Decimal | None = Field(default=None, ge=0)
    low_stock_threshold: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ItemOut(BaseModel):
    id: uuid.UUID
    sku: str
    name: str
    kind: str
    unit_code: str
    tracking: str
    barcode: str | None
    sale_price: MoneyStr | None
    avg_cost: MoneyStr | None
    low_stock_threshold: MoneyStr | None
    is_active: bool

    model_config = {"from_attributes": True}


class UnitOut(BaseModel):
    code: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class LocationIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "name": "Склад №2", "kind": "physical",  # physical | digital
    }}}

    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(pattern=r"^(physical|digital)$")


class LocationOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    is_transit: bool
    is_active: bool

    model_config = {"from_attributes": True}


# ---------- Schemas: склад ----------

class TransferIn(BaseModel):
    model_config = {"json_schema_extra": {"example": {
        "item_id": "uuid", "qty": "4",
        "from_location_id": "uuid-склад-1", "to_location_id": "uuid-склад-2",
        "note": "перемещение между складами",
    }}}

    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    serial_codes: list[str] | None = None
    counterparty_id: uuid.UUID | None = None
    moved_at: date | None = None
    note: str = ""


class AdjustmentLine(BaseModel):
    item_id: uuid.UUID
    qty_fact: Decimal | None = Field(default=None, ge=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)
    serial_codes: list[str] | None = None


class AdjustmentIn(BaseModel):
    # инвентаризация: строка = ФАКТ; излишек/недостача уйдут через транзит «Брак»;
    # для серийных товаров serial_codes — полный список кодов на локации
    model_config = {"json_schema_extra": {"example": {
        "location_id": "uuid",
        "lines": [
            {"item_id": "uuid", "qty_fact": "7", "unit_cost": "100"},
            {"item_id": "uuid-цифровой", "serial_codes": ["CODE-1", "CODE-2"]},
        ],
    }}}

    location_id: uuid.UUID
    lines: list[AdjustmentLine] = Field(min_length=1)
    moved_at: date | None = None
    note: str = ""


class MoveOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    qty: MoneyStr
    unit_cost: MoneyStr | None
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    counterparty_id: uuid.UUID | None
    source_type: str | None
    moved_at: date
    note: str


class BalanceOut(BaseModel):
    item_id: uuid.UUID
    sku: str
    item_name: str
    item_kind: str
    location_id: uuid.UUID
    location_name: str
    location_kind: str
    qty: MoneyStr
    avg_cost: MoneyStr | None
    value: MoneyStr | None


# ---------- НСИ ----------

@router.get("/items", response_model=list[ItemOut] | Page[ItemOut])
def list_items(
    user: CurrentUser,
    db: Session = Depends(get_db),
    kind: str | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    scoped: CompanyScoped = None,
    page: PageParams = Depends(page_params),
):
    # q — поиск по артикулу/имени (GIN pg_trgm, миграция 0021)
    query = select(m.Item).where(
        m.Item.company_id == scoped).order_by(m.Item.sku)
    if kind is not None:
        query = query.where(m.Item.kind == kind)
    if is_active is not None:
        query = query.where(m.Item.is_active == is_active)
    if q:
        query = query.where(or_(m.Item.sku.ilike(f"%{q}%"), m.Item.name.ilike(f"%{q}%")))
    return page.apply(db, query)


@router.get("/units", response_model=list[UnitOut])
def list_units(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(m.Unit).order_by(m.Unit.code)).all()


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemIn, user: WriteUser, db: Session = Depends(get_db),
                scoped: CompanyScoped = None):
    with svc():
        item = service.create_item(db, body.model_dump(), company_id=scoped)
    db.commit()
    db.refresh(item)
    return item


def _get_item(db: Session, item_id: uuid.UUID,
              scoped: uuid.UUID | None = None) -> m.Item:
    item = db.get(m.Item, item_id)
    if item is None or (scoped is not None and item.company_id != scoped):
        raise HTTPException(404, "Item not found")
    return item


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db),
             scoped: CompanyScoped = None):
    return _get_item(db, item_id, scoped)


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(
    item_id: uuid.UUID, body: ItemPatch, user: WriteUser, db: Session = Depends(get_db),
    scoped: CompanyScoped = None,
):
    item = _get_item(db, item_id, scoped)
    with svc():
        service.update_item(db, item, body.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(item)
    return item


@router.delete("/items/{item_id}")
def delete_item(item_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db),
               scoped: CompanyScoped = None):
    """Только неактивная номенклатура без движений (§6)."""
    item = _get_item(db, item_id, scoped)
    with svc():
        service.delete_item(db, item)
    db.commit()
    return {"ok": True}


@router.get("/locations", response_model=list[LocationOut] | Page[LocationOut])
def list_locations(user: CurrentUser, db: Session = Depends(get_db),
                   scoped: CompanyScoped = None,
                   page: PageParams = Depends(page_params)):
    return page.apply(db, select(m.Location).where(
        m.Location.company_id == scoped).order_by(m.Location.name))


@router.post("/locations", response_model=LocationOut, status_code=201)
def create_location(body: LocationIn, user: WriteUser, db: Session = Depends(get_db),
                    scoped: CompanyScoped = None):
    with svc():
        location = service.create_location(db, body.model_dump(), company_id=scoped)
    db.commit()
    db.refresh(location)
    return location


# ---------- Склад ----------

class SerialVoidIn(BaseModel):
    code: str = Field(min_length=3, max_length=255)
    note: str = Field(default="", max_length=200)

    model_config = {"json_schema_extra": {"example": {
        "code": "LICENSE-2026-001", "note": "ключ скомпрометирован",
    }}}


@router.post("/serials/void", response_model=MoveOut, status_code=201)
def void_serial(body: SerialVoidIn, user: User = Depends(require_module("accounting")),
                db: Session = Depends(get_db), scoped: CompanyScoped = None):
    """Д13: испортить цифровой код — движение в транзит «Брак» + void."""
    with svc():
        move = service.void_serial(db, code=body.code, user_id=user.id,
                                   note=body.note, company_id=scoped)
    db.commit()
    db.refresh(move)
    return service.move_payload(move)


@router.get("/stock/balances", response_model=list[BalanceOut] | Page[BalanceOut])
def stock_balances(
    user: CurrentUser,
    db: Session = Depends(get_db),
    on_date: date | None = None,
    location_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    scoped: CompanyScoped = None,
    page: PageParams = Depends(page_params),
):
    rows = service.stock_balances(
        db, on_date=on_date, location_id=location_id, item_id=item_id,
        company_id=scoped,
    )
    if not page.paginated:
        return rows
    total = len(rows)
    start = page.offset or 0
    end = start + page.limit if page.limit else None
    return {"items": rows[start:end], "total": total}


@router.get("/stock/moves", response_model=list[MoveOut] | Page[MoveOut])
def stock_moves(
    user: CurrentUser,
    db: Session = Depends(get_db),
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    scoped: CompanyScoped = None,
    page: PageParams = Depends(page_params),
):
    query = service.stock_moves_query(
        item_id=item_id, location_id=location_id,
        date_from=date_from, date_to=date_to, company_id=scoped,
    )
    if page.paginated:
        total = db.scalar(select(func.count()).select_from(
            query.order_by(None).subquery())) or 0
        paged = query.offset(page.offset or 0)
        if page.limit:
            paged = paged.limit(page.limit)
        return {"items": [service.move_payload(mv) for mv in db.scalars(paged).all()],
                "total": int(total)}
    return [service.move_payload(mv) for mv in db.scalars(query).all()]


@router.post("/stock/transfer", response_model=MoveOut, status_code=201)
def transfer_stock(body: TransferIn, user: WriteUser, db: Session = Depends(get_db),
                   scoped: CompanyScoped = None):
    with svc():
        move = service.transfer_stock(db, user_id=user.id,
                                      data={**body.model_dump(), "company_id": scoped})
    db.commit()
    db.refresh(move)
    return service.move_payload(move)


@router.post("/stock/adjustment", response_model=list[MoveOut], status_code=201)
def adjustment(body: AdjustmentIn, user: WriteUser, db: Session = Depends(get_db),
               scoped: CompanyScoped = None):
    with svc():
        moves = service.adjustment(db, user_id=user.id,
                                   data={**body.model_dump(), "company_id": scoped})
    db.commit()
    for move in moves:
        db.refresh(move)
    return [service.move_payload(move) for move in moves]
