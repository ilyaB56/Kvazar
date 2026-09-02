"""API mini_crm: /api/v1/crm/... (этапы A–C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, BeforeValidator
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Annotated

from src.core.auth import AdminUser, WriteUser
from src.core.models import RecordVersion
from src.db import get_db
from src.modules.mini_crm import models as m
from src.modules.mini_crm import service

router = APIRouter(tags=["crm"])

MoneyStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, Decimal) else v)]


@contextmanager
def svc():
    try:
        yield
    except service.CrmError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------- Schemas ----------

class StageIn(BaseModel):
    name: str
    position: int
    probability: int | None = None
    is_won: bool = False
    is_lost: bool = False


class StagePatch(BaseModel):
    name: str | None = None
    position: int | None = None
    probability: int | None = None
    is_active: bool | None = None


class StageOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    probability: int | None
    is_won: bool
    is_lost: bool
    is_active: bool

    model_config = {"from_attributes": True}


class DealIn(BaseModel):
    title: str
    stage_id: uuid.UUID
    amount: Decimal
    currency: str = "RUB"
    counterparty_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    responsible_id: uuid.UUID | None = None
    expected_close_at: object = None
    dimensions: dict = {}


class DealPatch(BaseModel):
    title: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    counterparty_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    responsible_id: uuid.UUID | None = None
    expected_close_at: object = None
    dimensions: dict | None = None


class DealOut(BaseModel):
    id: uuid.UUID
    title: str
    stage_id: uuid.UUID
    counterparty_id: uuid.UUID | None
    counterparty_name: str | None = None
    contact_id: uuid.UUID | None
    responsible_id: uuid.UUID | None
    amount: MoneyStr
    currency: str
    rate: MoneyStr | None
    amount_base: MoneyStr | None
    expected_close_at: object = None
    lost_reason: str | None
    dimensions: dict
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MoveIn(BaseModel):
    stage_id: uuid.UUID


class ReasonIn(BaseModel):
    reason: str


# ---------- Стадии ----------

@router.get("/stages", response_model=list[StageOut])
def list_stages(user: WriteUser, db: Session = Depends(get_db)):
    return db.scalars(select(m.Stage).order_by(m.Stage.position)).all()


@router.post("/stages", response_model=StageOut, status_code=201)
def create_stage(body: StageIn, admin: AdminUser, db: Session = Depends(get_db)):
    if db.scalar(select(m.Stage).where(m.Stage.position == body.position)):
        raise HTTPException(409, f"Stage position {body.position} already exists")
    stage = m.Stage(**body.model_dump())
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return stage


@router.patch("/stages/{stage_id}", response_model=StageOut)
def patch_stage(stage_id: uuid.UUID, body: StagePatch, admin: AdminUser,
                db: Session = Depends(get_db)):
    stage = db.get(m.Stage, stage_id)
    if stage is None:
        raise HTTPException(404, "Stage not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(stage, field, value)
    db.commit()
    db.refresh(stage)
    return stage


@router.delete("/stages/{stage_id}")
def delete_stage(stage_id: uuid.UUID, admin: AdminUser, db: Session = Depends(get_db)):
    stage = db.get(m.Stage, stage_id)
    if stage is None:
        raise HTTPException(404, "Stage not found")
    has_deals = db.scalar(select(m.Deal.id).where(m.Deal.stage_id == stage_id).limit(1))
    if has_deals:
        raise HTTPException(422, "Stage has deals; move them first")
    db.delete(stage)
    db.commit()
    return {"ok": True}


# ---------- Сделки ----------

def _enrich(db: Session, deals: list[m.Deal]) -> list[dict]:
    names = service.counterparty_names(db, [d.counterparty_id for d in deals])
    return [
        {
            "id": d.id, "title": d.title, "stage_id": d.stage_id,
            "counterparty_id": d.counterparty_id,
            "counterparty_name": names.get(str(d.counterparty_id)) if d.counterparty_id else None,
            "contact_id": d.contact_id, "responsible_id": d.responsible_id,
            "amount": d.amount, "currency": d.currency,
            "rate": d.rate, "amount_base": d.amount_base,
            "expected_close_at": d.expected_close_at, "lost_reason": d.lost_reason,
            "dimensions": d.dimensions, "is_deleted": d.is_deleted, "created_at": d.created_at,
        }
        for d in deals
    ]


@router.get("/deals", response_model=list[DealOut])
def list_deals(user: WriteUser, q: str | None = None, stage_id: uuid.UUID | None = None,
               db: Session = Depends(get_db)):
    query = select(m.Deal).where(m.Deal.is_deleted.is_(False)).order_by(m.Deal.created_at.desc())
    if stage_id:
        query = query.where(m.Deal.stage_id == stage_id)
    if q:
        # ILIKE по части названия; GIN pg_trgm (gin_trgm_ops) ускоряет и ILIKE
        query = query.where(m.Deal.title.ilike(f"%{q}%"))
    return _enrich(db, db.scalars(query).all())


def _get_deal(db: Session, deal_id: uuid.UUID) -> m.Deal:
    deal = db.get(m.Deal, deal_id)
    if deal is None or deal.is_deleted:
        raise HTTPException(404, "Deal not found")
    return deal


@router.post("/deals", response_model=DealOut, status_code=201)
def create_deal(body: DealIn, user: WriteUser, db: Session = Depends(get_db)):
    with svc():
        deal = service.create_deal(db, user_id=user.id, data=body.model_dump())
    db.commit()
    db.refresh(deal)
    return _enrich(db, [deal])[0]


@router.get("/deals/{deal_id}", response_model=DealOut)
def get_deal(deal_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    return _enrich(db, [_get_deal(db, deal_id)])[0]


@router.patch("/deals/{deal_id}", response_model=DealOut)
def patch_deal(deal_id: uuid.UUID, body: DealPatch, user: WriteUser,
               db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    with svc():
        service.update_deal(db, deal, user_id=user.id,
                            changes=body.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(deal)
    return _enrich(db, [deal])[0]


@router.post("/deals/{deal_id}/move", response_model=DealOut)
def move_deal(deal_id: uuid.UUID, body: MoveIn, user: WriteUser,
              db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    with svc():
        service.move_deal(db, deal, user_id=user.id, to_stage_id=body.stage_id)
    db.commit()
    db.refresh(deal)
    return _enrich(db, [deal])[0]


@router.post("/deals/{deal_id}/delete-mark", response_model=DealOut)
def delete_mark(deal_id: uuid.UUID, body: ReasonIn, admin: AdminUser,
                db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    with svc():
        service.mark_deleted(db, deal, user_id=admin.id, reason=body.reason)
    db.commit()
    db.refresh(deal)
    return _enrich(db, [deal])[0]


@router.get("/deals/{deal_id}/counterparty")
def deal_counterparty(deal_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    if not deal.counterparty_id:
        return {"counterparty_id": None, "name": None}
    names = service.counterparty_names(db, [deal.counterparty_id])
    return {"counterparty_id": str(deal.counterparty_id),
            "name": names.get(str(deal.counterparty_id))}


@router.get("/history/{entity_type}/{entity_id}")
def crm_history(entity_type: str, entity_id: str, user: WriteUser,
                db: Session = Depends(get_db)):
    rows = db.scalars(
        select(RecordVersion)
        .where(RecordVersion.entity_type == entity_type, RecordVersion.entity_id == entity_id)
        .order_by(RecordVersion.id.desc())
    ).all()
    return [
        {"changed_by": str(r.changed_by) if r.changed_by else None,
         "changed_at": r.changed_at.isoformat(), "diff": r.diff, "reason": r.reason}
        for r in rows
    ]


# ---------- Коммуникации и задачи (этап B) ----------

class CommunicationIn(BaseModel):
    kind: str = "note"
    content: str
    occurred_at: object = None


class CommunicationOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    kind: str
    content: str
    occurred_at: object = None
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityIn(BaseModel):
    title: str
    due_at: object


class ActivityPatch(BaseModel):
    title: str | None = None
    due_at: object = None
    done: bool | None = None


class ActivityOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    title: str
    due_at: object = None
    done: bool
    done_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/deals/{deal_id}/communications", response_model=list[CommunicationOut])
def list_communications(deal_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    _get_deal(db, deal_id)
    return db.scalars(select(m.Communication).where(m.Communication.deal_id == deal_id)
                      .order_by(m.Communication.occurred_at.desc())).all()


@router.post("/deals/{deal_id}/communications", response_model=CommunicationOut, status_code=201)
def create_communication(deal_id: uuid.UUID, body: CommunicationIn, user: WriteUser,
                         db: Session = Depends(get_db)):
    from datetime import date as date_type

    _get_deal(db, deal_id)
    if body.kind not in ("call", "email", "meeting", "note", "other"):
        raise HTTPException(422, "kind must be call|email|meeting|note|other")
    occurred = body.occurred_at or date_type.today()
    if isinstance(occurred, str):
        occurred = date_type.fromisoformat(occurred)
    row = m.Communication(deal_id=deal_id, kind=body.kind, content=body.content,
                          occurred_at=occurred, created_by=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/deals/{deal_id}/activities", response_model=list[ActivityOut])
def list_deal_activities(deal_id: uuid.UUID, user: WriteUser, db: Session = Depends(get_db)):
    _get_deal(db, deal_id)
    return db.scalars(select(m.Activity).where(m.Activity.deal_id == deal_id)
                      .order_by(m.Activity.due_at)).all()


def _create_activity(db: Session, deal_id: uuid.UUID, body: ActivityIn,
                     user_id: uuid.UUID) -> m.Activity:
    from datetime import date as date_type

    due = body.due_at
    if isinstance(due, str):
        due = date_type.fromisoformat(due)
    if not isinstance(due, date_type):
        raise HTTPException(422, "due_at must be a date")
    row = m.Activity(deal_id=deal_id, title=body.title, due_at=due, created_by=user_id)
    db.add(row)
    db.flush()
    from src.core import events

    events.publish(db, "crm.activity.created", {
        "activity_id": str(row.id), "deal_id": str(deal_id),
        "title": row.title, "due_at": row.due_at.isoformat(),
    })
    return row


@router.post("/deals/{deal_id}/activities", response_model=ActivityOut, status_code=201)
def create_activity(deal_id: uuid.UUID, body: ActivityIn, user: WriteUser,
                    db: Session = Depends(get_db)):
    _get_deal(db, deal_id)
    row = _create_activity(db, deal_id, body, user.id)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/activities/{activity_id}", response_model=ActivityOut)
def patch_activity(activity_id: uuid.UUID, body: ActivityPatch, user: WriteUser,
                   db: Session = Depends(get_db)):
    from datetime import date as date_type

    row = db.get(m.Activity, activity_id)
    if row is None:
        raise HTTPException(404, "Activity not found")
    changes = body.model_dump(exclude_unset=True)
    if "done" in changes:
        row.done = changes.pop("done")
        from datetime import UTC, datetime as dt

        row.done_at = dt.now(UTC) if row.done else None
    if "due_at" in changes and changes["due_at"] is not None:
        due = changes["due_at"]
        if isinstance(due, str):
            due = date_type.fromisoformat(due)
        row.due_at = due
    if changes.get("title"):
        row.title = changes["title"]
    db.commit()
    db.refresh(row)
    return row


@router.get("/activities", response_model=list[ActivityOut])
def list_activities(user: WriteUser, db: Session = Depends(get_db),
                    due_before: object = None, status: str | None = None,
                    responsible_id: uuid.UUID | None = None):
    """Общий список задач: свои + все для админа; фильтры due_before/status/responsible."""
    from datetime import date as date_type

    is_admin = getattr(user, "role", "user") == "admin"
    query = select(m.Activity).join(m.Deal, m.Deal.id == m.Activity.deal_id).where(
        m.Deal.is_deleted.is_(False))
    if not is_admin:
        query = query.where(m.Deal.responsible_id == user.id)
    elif responsible_id:
        query = query.where(m.Deal.responsible_id == responsible_id)
    if due_before is not None:
        before = date_type.fromisoformat(due_before) if isinstance(due_before, str) else due_before
        query = query.where(m.Activity.due_at <= before)
    if status == "open":
        query = query.where(m.Activity.done.is_(False))
    elif status == "done":
        query = query.where(m.Activity.done.is_(True))
    return db.scalars(query.order_by(m.Activity.due_at)).all()
