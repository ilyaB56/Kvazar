"""API модуля учёта: /api/v1/accounting/... (весь — под авторизацией)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.core.auth import AdminUser, require_module
from src.core.models import User
from src.core.models import RecordVersion
from src.db import get_db
from src.modules.mgmt_accounting import models as m
from src.modules.mgmt_accounting import service

router = APIRouter(tags=["accounting"])

# Деньги и курсы в ответах — строками (точность Decimal без float, ADR-003)
MoneyStr = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, Decimal) else v)]


@contextmanager
def svc():
    """Перевод бизнес-ошибок сервисного слоя в HTTP-ответы."""
    try:
        yield
    except service.AccountingError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------- Schemas ----------

class AccountIn(BaseModel):
    name: str
    currency: str = Field(default=service.BASE_CURRENCY, pattern=r"^[A-Z]{3}$")
    company_id: uuid.UUID | None = None
    account_number: str | None = None


class AccountPatch(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    account_number: str | None = None


class AccountOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    name: str
    currency: str
    account_number: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class CategoryIn(BaseModel):
    name: str
    kind: str = Field(pattern=r"^(income|expense|transfer)$")
    parent_id: uuid.UUID | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    is_active: bool

    model_config = {"from_attributes": True}


class CounterpartyIn(BaseModel):
    name: str
    inn: str = ""
    kpp: str = ""
    contact_id: uuid.UUID | None = None


class CounterpartyOut(BaseModel):
    id: uuid.UUID
    internal_code: str
    name: str
    inn: str
    kpp: str
    contact_id: uuid.UUID | None
    is_active: bool
    warning: str | None = None

    model_config = {"from_attributes": True}


class TransactionIn(BaseModel):
    kind: str = Field(pattern=r"^(income|expense|transfer)$")
    operated_at: date
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    account_id: uuid.UUID
    account_to_id: uuid.UUID | None = None
    amount_to: Decimal | None = Field(default=None, gt=0)
    category_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None
    description: str = ""
    dimensions: dict = {}
    post_immediately: bool = False


class TransactionPatch(BaseModel):
    operated_at: date | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    account_id: uuid.UUID | None = None
    account_to_id: uuid.UUID | None = None
    amount_to: Decimal | None = Field(default=None, gt=0)
    category_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None
    description: str | None = None
    dimensions: dict | None = None


class TransactionOut(BaseModel):
    id: uuid.UUID
    doc_number: str | None
    doc_type_code: str
    kind: str
    status: str
    operated_at: date
    created_at: datetime
    amount: MoneyStr
    currency: str
    rate: MoneyStr | None
    amount_base: MoneyStr | None
    amount_to: MoneyStr | None
    currency_to: str | None
    rate_to: MoneyStr | None
    amount_to_base: MoneyStr | None
    account_id: uuid.UUID
    account_to_id: uuid.UUID | None
    category_id: uuid.UUID | None
    counterparty_id: uuid.UUID | None
    description: str
    dimensions: dict
    is_deleted: bool
    is_stornoed: bool
    storno_of_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class RateIn(BaseModel):
    date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Decimal = Field(gt=0)


class RateOut(BaseModel):
    date: date
    currency: str
    rate: MoneyStr
    source: str

    model_config = {"from_attributes": True}


class PeriodOut(BaseModel):
    year: int
    month: int
    status: str
    closed_by: uuid.UUID | None
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1)


class OptionalReasonIn(BaseModel):
    reason: str | None = None


class CashflowTotal(BaseModel):
    kind: str
    category_id: uuid.UUID | None
    total: MoneyStr


class CashflowOut(BaseModel):
    date_from: date
    date_to: date
    account_id: uuid.UUID | None
    opening_balance: MoneyStr
    closing_balance: MoneyStr
    totals: list[CashflowTotal]


# ---------- Справочники ----------

@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(user: User = Depends(require_module("accounting", "ro")), db: Session = Depends(get_db),
                  q: str | None = None):
    query = select(m.Account).order_by(m.Account.name)
    if q:
        query = query.where(m.Account.name.ilike(f"%{q}%"))  # GIN pg_trgm
    return db.scalars(query).all()


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    account = m.Account(name=body.name, currency=body.currency, company_id=body.company_id,
                        account_number=body.account_number)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def patch_account(
    account_id: uuid.UUID, body: AccountPatch, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)
):
    account = db.get(m.Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    with svc():
        service.patch_account(db, account, user_id=user.id, changes=body.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(account)
    return account


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: User = Depends(require_module("accounting", "ro")), db: Session = Depends(get_db),
                    q: str | None = None):
    query = select(m.Category).order_by(m.Category.name)
    if q:
        query = query.where(m.Category.name.ilike(f"%{q}%"))
    return db.scalars(query).all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    if body.parent_id is not None and db.get(m.Category, body.parent_id) is None:
        raise HTTPException(422, f"Unknown parent category: {body.parent_id}")
    category = m.Category(**body.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.get("/counterparties", response_model=list[CounterpartyOut])
def list_counterparties(user: User = Depends(require_module("accounting", "ro")), db: Session = Depends(get_db),
                        q: str | None = None):
    query = select(m.Counterparty).order_by(m.Counterparty.name)
    if q:
        query = query.where(m.Counterparty.name.ilike(f"%{q}%"))
    return db.scalars(query).all()


@router.post("/counterparties", response_model=CounterpartyOut, status_code=201)
def create_counterparty(body: CounterpartyIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    with svc():
        counterparty, warning = service.create_counterparty(db, body.model_dump())
    db.commit()
    db.refresh(counterparty)
    return CounterpartyOut.model_validate(counterparty).model_copy(update={"warning": warning})


# ---------- Транзакции ----------

@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    user: User = Depends(require_module("accounting", "ro")),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    status: str | None = None,
    kind: str | None = None,
):
    query = select(m.Transaction).order_by(m.Transaction.operated_at, m.Transaction.created_at)
    if date_from is not None:
        query = query.where(m.Transaction.operated_at >= date_from)
    if date_to is not None:
        query = query.where(m.Transaction.operated_at <= date_to)
    if account_id is not None:
        query = query.where(or_(
            m.Transaction.account_id == account_id,
            m.Transaction.account_to_id == account_id,
        ))
    if category_id is not None:
        query = query.where(m.Transaction.category_id == category_id)
    if status is not None:
        query = query.where(m.Transaction.status == status)
    if kind is not None:
        query = query.where(m.Transaction.kind == kind)
    return db.scalars(query).all()


def _get_transaction(db: Session, txn_id: uuid.UUID) -> m.Transaction:
    txn = db.get(m.Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    return txn


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    with svc():
        txn = service.create_transaction(
            db, user_id=user.id, data=body.model_dump(exclude={"post_immediately"})
        )
        if body.post_immediately:
            service.post_transaction(db, txn)
    db.commit()
    db.refresh(txn)
    return txn


@router.patch("/transactions/{txn_id}", response_model=TransactionOut)
def patch_transaction(
    txn_id: uuid.UUID, body: TransactionPatch, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)
):
    txn = _get_transaction(db, txn_id)
    with svc():
        service.update_transaction(
            db, txn, user_id=user.id, changes=body.model_dump(exclude_unset=True)
        )
    db.commit()
    db.refresh(txn)
    return txn


@router.delete("/transactions/{txn_id}")
def delete_draft(txn_id: uuid.UUID, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    """Физическое удаление — единственное, и только для черновиков."""
    txn = _get_transaction(db, txn_id)
    if txn.status != "draft":
        raise HTTPException(409, "Only drafts can be deleted physically")
    db.delete(txn)
    db.commit()
    return {"ok": True}


@router.post("/transactions/{txn_id}/post", response_model=TransactionOut)
def post_transaction(txn_id: uuid.UUID, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    txn = _get_transaction(db, txn_id)
    with svc():
        service.post_transaction(db, txn)
    db.commit()
    db.refresh(txn)
    return txn


@router.post("/transactions/{txn_id}/storno", response_model=TransactionOut)
def storno_transaction(
    txn_id: uuid.UUID, body: ReasonIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)
):
    txn = _get_transaction(db, txn_id)
    with svc():
        storno = service.create_storno(db, txn, user_id=user.id, reason=body.reason)
    db.commit()
    db.refresh(storno)
    return storno


@router.post("/transactions/{txn_id}/delete-mark", response_model=TransactionOut)
def delete_mark(txn_id: uuid.UUID, body: ReasonIn, admin: AdminUser, db: Session = Depends(get_db)):
    txn = _get_transaction(db, txn_id)
    with svc():
        service.mark_deleted(db, txn, user_id=admin.id, reason=body.reason)
    db.commit()
    db.refresh(txn)
    return txn


@router.post("/transactions/{txn_id}/delete-unmark", response_model=TransactionOut)
def delete_unmark(
    txn_id: uuid.UUID, body: OptionalReasonIn, admin: AdminUser, db: Session = Depends(get_db)
):
    txn = _get_transaction(db, txn_id)
    with svc():
        service.unmark_deleted(db, txn, user_id=admin.id, reason=body.reason)
    db.commit()
    db.refresh(txn)
    return txn


# ---------- Отчёты ----------

@router.get("/report/cashflow", response_model=CashflowOut)
def cashflow_report(
    user: User = Depends(require_module("accounting", "ro")),
    db: Session = Depends(get_db),
    date_from: date = Query(...),
    date_to: date = Query(...),
    account_id: uuid.UUID | None = None,
):
    if date_to < date_from:
        raise HTTPException(422, "date_to must be greater than or equal to date_from")
    return service.cashflow(db, date_from, date_to, account_id)


@router.get("/export/client-bank")
def export_client_bank(
    user: User = Depends(require_module("accounting", "ro")),
    db: Session = Depends(get_db),
    date_from: date = Query(...),
    date_to: date = Query(...),
    account_id: uuid.UUID = Query(...),
):
    """Выгрузка 1CClientBankExchange (cp1251), 1С:Бухгалтерия грузит как выписку."""
    from fastapi import Response

    with svc():
        text = service.export_client_bank(db, date_from, date_to, account_id)
    stamp = date_from.strftime("%Y%m%d")
    return Response(
        content=text.encode("windows-1251", errors="replace"),
        media_type="text/plain; charset=windows-1251",
        headers={"Content-Disposition":
                 f'attachment; filename="1c-exchange-{stamp}.txt"'},
    )


# ---------- Курсы ----------

@router.get("/rates", response_model=list[RateOut])
def list_rates(
    user: User = Depends(require_module("accounting", "ro")),
    db: Session = Depends(get_db),
    currency: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    query = select(m.Rate).order_by(m.Rate.date.desc(), m.Rate.currency)
    if currency is not None:
        query = query.where(m.Rate.currency == currency)
    if date_from is not None:
        query = query.where(m.Rate.date >= date_from)
    if date_to is not None:
        query = query.where(m.Rate.date <= date_to)
    return db.scalars(query).all()


@router.post("/rates", response_model=RateOut)
def upsert_rate(body: RateIn, user: User = Depends(require_module("accounting")), db: Session = Depends(get_db)):
    rate = service.upsert_rate(db, body.date, body.currency, body.rate)
    db.commit()
    db.refresh(rate)
    return rate


# ---------- Периоды ----------

@router.get("/periods", response_model=list[PeriodOut])
def list_periods(user: User = Depends(require_module("accounting", "ro")), db: Session = Depends(get_db)):
    return db.scalars(select(m.Period).order_by(m.Period.year, m.Period.month)).all()


def _check_month(year: int, month: int) -> None:
    if not 1 <= month <= 12:
        raise HTTPException(422, "month must be between 1 and 12")


@router.post("/periods/{year}/{month}/close", response_model=PeriodOut)
def close_period(
    year: int,
    month: int,
    admin: AdminUser,
    body: OptionalReasonIn | None = None,
    db: Session = Depends(get_db),
):
    _check_month(year, month)
    reason = body.reason if body is not None else None
    with svc():
        period = service.close_period(db, year, month, user_id=admin.id, reason=reason)
    db.commit()
    db.refresh(period)
    return period


@router.post("/periods/{year}/{month}/reopen", response_model=PeriodOut)
def reopen_period(
    year: int,
    month: int,
    admin: AdminUser,
    body: OptionalReasonIn | None = None,
    db: Session = Depends(get_db),
):
    _check_month(year, month)
    reason = body.reason if body is not None else None
    with svc():
        period = service.reopen_period(db, year, month, user_id=admin.id, reason=reason)
    db.commit()
    db.refresh(period)
    return period


# ---------- История версий ----------

@router.get("/history/{entity_type}/{entity_id}")
def history(entity_type: str, entity_id: str, user: User = Depends(require_module("accounting", "ro")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(RecordVersion)
        .where(RecordVersion.entity_type == entity_type, RecordVersion.entity_id == entity_id)
        .order_by(RecordVersion.id.desc())
    ).all()
    return [
        {
            "changed_by": str(row.changed_by) if row.changed_by else None,
            "changed_at": row.changed_at.isoformat(),
            "diff": row.diff,
            "reason": row.reason,
        }
        for row in rows
    ]
