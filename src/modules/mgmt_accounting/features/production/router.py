"""API фичи production (resources-core §3.5, §6): тех.карты и заказы на сборку.

GET — CurrentUser, мутации — WriteUser (§8). Деньги/количества строками.
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

from src.core.auth import CurrentUser, WriteUser
from src.db import get_db
from src.modules.mgmt_accounting.features.production import models as m
from src.modules.mgmt_accounting.features.production import service
from src.modules.mgmt_accounting.service import AccountingError

router = APIRouter(tags=["production"])

MoneyStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, Decimal) else v)]


@contextmanager
def svc():
    """Перевод бизнес-ошибок сервисного слоя в HTTP-ответы."""
    try:
        yield
    except AccountingError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------- Schemas ----------

class ComponentIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)


class TechCardIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    product_item_id: uuid.UUID
    qty_out: Decimal = Field(gt=0)
    components: list[ComponentIn] = Field(min_length=1)


class TechCardOut(BaseModel):
    id: uuid.UUID
    name: str
    product_item_id: uuid.UUID
    qty_out: MoneyStr
    components: list[dict]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductionOrderIn(BaseModel):
    tech_card_id: uuid.UUID
    qty_planned: Decimal = Field(gt=0)
    moved_at: date | None = None
    note: str = ""


class ProductionOrderOut(BaseModel):
    id: uuid.UUID
    number: str | None
    tech_card_id: uuid.UUID
    qty_planned: MoneyStr
    status: str
    is_stornoed: bool
    material_cost: MoneyStr | None
    moved_at: date
    note: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1)


# ---------- Тех.карты ----------

@router.get("/tech-cards", response_model=list[TechCardOut])
def list_tech_cards(
    user: CurrentUser,
    db: Session = Depends(get_db),
    is_active: bool | None = None,
):
    query = select(m.TechCard).order_by(m.TechCard.created_at.desc())
    if is_active is not None:
        query = query.where(m.TechCard.is_active == is_active)
    return db.scalars(query).all()


@router.post("/tech-cards", response_model=TechCardOut, status_code=201)
def create_tech_card(body: TechCardIn, user: WriteUser, db: Session = Depends(get_db)):
    with svc():
        card = service.create_tech_card(db, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(card)
    return card


# ---------- Заказы на сборку ----------

def _get_order(db: Session, order_id: uuid.UUID) -> m.ProductionOrder:
    order = db.get(m.ProductionOrder, order_id)
    if order is None:
        raise HTTPException(404, "Production order not found")
    return order


@router.get("/production-orders", response_model=list[ProductionOrderOut])
def list_orders(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    tech_card_id: uuid.UUID | None = None,
):
    query = select(m.ProductionOrder).order_by(m.ProductionOrder.created_at.desc())
    if status is not None:
        query = query.where(m.ProductionOrder.status == status)
    if tech_card_id is not None:
        query = query.where(m.ProductionOrder.tech_card_id == tech_card_id)
    return db.scalars(query).all()


@router.post("/production-orders", response_model=ProductionOrderOut, status_code=201)
def create_order(body: ProductionOrderIn, user: WriteUser, db: Session = Depends(get_db)):
    with svc():
        order = service.create_order(db, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(order)
    return order


@router.post("/production-orders/{order_id}/post", response_model=ProductionOrderOut)
def post_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    with svc():
        service.post_order(db, order)
    db.commit()
    return order


@router.post("/production-orders/{order_id}/unpost", response_model=ProductionOrderOut)
def unpost_order(
    order_id: uuid.UUID, body: ReasonIn, user: WriteUser, db: Session = Depends(get_db)
):
    """Сторно сборки — по правилам сторно приёмок (§7-D)."""
    order = _get_order(db, order_id)
    with svc():
        service.unpost_order(db, order, user_id=user.id, reason=body.reason)
    db.commit()
    return order


@router.post("/production-orders/{order_id}/cancel", response_model=ProductionOrderOut)
def cancel_order(order_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    with svc():
        service.cancel_order(db, order, user_id=user.id)
    db.commit()
    return order
