"""Сервисный слой учёта: проведение, сторно, периоды, версии, отчёт.

Инварианты (ADR-003): только Decimal; amount_base = amount × rate с округлением
half-up до копеек; курс замораживается при проведении и хранится в документе.
Изменения сущностей журналируются в erp_core.record_versions (core.versioning).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core import events
from src.core.versioning import record_version
from src.db import SessionLocal
from src.modules.mgmt_accounting import models as m

# Базовая (отчётная) валюта. Поле «валюта компании» появится позже; курс базовой
# валюты всегда 1 и строки в rates не требует.
BASE_CURRENCY = "RUB"
CENT = Decimal("0.01")

DOC_KIND = {"income": "ПК", "expense": "СК", "transfer": "ПР"}
STORNO_DOC_TYPE = "СТ"
INVERSE_KIND = {"income": "expense", "expense": "income", "transfer": "transfer"}


class AccountingError(Exception):
    """Нарушение бизнес-правил; status — код HTTP для роутера."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- Расчёты (чистые функции) ----------

def quantize2(value: Decimal) -> Decimal:
    """Округление до копеек, «от половины вверх» (half-up, ADR-003)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def compute_base(amount: Decimal, rate: Decimal) -> Decimal:
    return quantize2(amount * rate)


def format_doc_number(prefix: str, year: int, number: int) -> str:
    return f"{prefix}-{year:04d}-{number:05d}"


# ---------- Периоды ----------

def get_or_create_period(db: Session, year: int, month: int) -> m.Period:
    period = db.scalar(select(m.Period).where(m.Period.year == year, m.Period.month == month))
    if period is not None:
        return period
    # гонка «первой операции месяца»: вставляем без конфликта и перечитываем
    db.execute(
        pg_insert(m.Period)
        .values(year=year, month=month, status="open")
        .on_conflict_do_nothing(index_elements=["year", "month"])
    )
    period = db.scalar(select(m.Period).where(m.Period.year == year, m.Period.month == month))
    if period is not None:
        return period
    # конкурент откатился — создаём обычной вставкой
    period = m.Period(year=year, month=month, status="open")
    db.add(period)
    db.flush()
    return period


def ensure_period_open(db: Session, operated_at: date) -> None:
    period = db.scalar(select(m.Period).where(
        m.Period.year == operated_at.year, m.Period.month == operated_at.month
    ))
    if period is not None and period.status == "closed":
        raise AccountingError(422, f"Period {operated_at.strftime('%Y-%m')} is closed")


def close_period(
    db: Session, year: int, month: int, *, user_id: uuid.UUID, reason: str | None
) -> m.Period:
    period = get_or_create_period(db, year, month)
    if period.status == "closed":
        raise AccountingError(409, f"Period {year:04d}-{month:02d} is already closed")
    period.status = "closed"
    period.closed_by = user_id
    period.closed_at = datetime.now(UTC)
    db.flush()
    record_version(db, "acc.period", f"{year:04d}-{month:02d}", user_id,
                   {"status": {"old": "open", "new": "closed"}}, reason=reason)
    events.publish(db, "acc.period.closed", {"year": year, "month": month})
    return period


def reopen_period(
    db: Session, year: int, month: int, *, user_id: uuid.UUID, reason: str | None
) -> m.Period:
    period = get_or_create_period(db, year, month)
    if period.status == "open":
        raise AccountingError(409, f"Period {year:04d}-{month:02d} is not closed")
    period.status = "open"
    period.closed_by = None
    period.closed_at = None
    db.flush()
    record_version(db, "acc.period", f"{year:04d}-{month:02d}", user_id,
                   {"status": {"old": "closed", "new": "open"}}, reason=reason)
    events.publish(db, "acc.period.reopened", {"year": year, "month": month})
    return period


# ---------- Нумерация ----------

def next_doc_number(db: Session, doc_type_code: str, operated_at: date) -> str:
    """Выделить номер документа: SELECT ... FOR UPDATE в транзакции проведения."""
    doc_type = db.get(m.DocType, doc_type_code)
    if doc_type is None or not doc_type.is_active:
        raise AccountingError(422, f"Unknown doc type: {doc_type_code}")
    year = operated_at.year
    conditions = (m.DocSequence.doc_type_code == doc_type_code, m.DocSequence.year == year)
    seq = db.execute(
        select(m.DocSequence).where(*conditions).with_for_update()
    ).scalar_one_or_none()
    if seq is None:
        # гонка «первого номера года»: вставляем без конфликта, затем захватываем
        # существующую строку блокировкой
        db.execute(
            pg_insert(m.DocSequence)
            .values(doc_type_code=doc_type_code, year=year, last_number=0)
            .on_conflict_do_nothing(index_elements=["doc_type_code", "year"])
        )
        seq = db.execute(
            select(m.DocSequence).where(*conditions).with_for_update()
        ).scalar_one()
    seq.last_number += 1
    db.flush()
    return format_doc_number(doc_type.number_prefix, year, seq.last_number)


# ---------- Курсы ----------

def rate_for(db: Session, operated_at: date, currency: str) -> Decimal:
    """Курс к базовой валюте на дату операции; базовая валюта — всегда 1."""
    if currency == BASE_CURRENCY:
        return Decimal(1)
    rate = db.scalar(select(m.Rate).where(m.Rate.date == operated_at, m.Rate.currency == currency))
    if rate is None:
        raise AccountingError(
            422,
            f"No rate for {currency} on {operated_at.isoformat()}: "
            "set it via POST /api/v1/accounting/rates",
        )
    return rate.rate


def upsert_rate(db: Session, day: date, currency: str, rate: Decimal) -> m.Rate:
    row = db.scalar(select(m.Rate).where(m.Rate.date == day, m.Rate.currency == currency))
    if row is None:
        row = m.Rate(date=day, currency=currency, rate=rate, source="manual")
        db.add(row)
    else:
        row.rate = rate
        row.source = "manual"
    return row


# ---------- Транзакции ----------

def _get_active(db: Session, model, ref_id: uuid.UUID, label: str):
    obj = db.get(model, ref_id)
    if obj is None or not obj.is_active:
        raise AccountingError(422, f"Unknown or inactive {label}: {ref_id}")
    return obj


def validate_transaction(db: Session, txn: m.Transaction) -> None:
    """Ссылочная и валюная целостность; вызывается при создании и каждой правке."""
    if txn.amount is None or txn.amount <= 0:
        raise AccountingError(422, "amount must be positive")
    account = _get_active(db, m.Account, txn.account_id, "account")
    if txn.currency != account.currency:
        raise AccountingError(
            422, f"currency {txn.currency} does not match account currency {account.currency}"
        )
    if txn.kind == "transfer":
        if txn.account_to_id is None:
            raise AccountingError(422, "account_to_id is required for transfer")
        account_to = _get_active(db, m.Account, txn.account_to_id, "account_to")
        if txn.account_to_id == txn.account_id:
            raise AccountingError(422, "transfer to the same account is not allowed")
        txn.currency_to = account_to.currency
    else:
        txn.account_to_id = None
        txn.currency_to = None
        txn.amount_to = None
    if txn.category_id is not None:
        category = _get_active(db, m.Category, txn.category_id, "category")
        if category.kind != txn.kind:
            raise AccountingError(
                422, f"category kind {category.kind} does not match transaction kind {txn.kind}"
            )
    if txn.counterparty_id is not None:
        _get_active(db, m.Counterparty, txn.counterparty_id, "counterparty")


def create_transaction(db: Session, *, user_id: uuid.UUID, data: dict) -> m.Transaction:
    """Черновик: номера не потребляет, но период уже проверяет и создаёт лениво."""
    ensure_period_open(db, data["operated_at"])
    get_or_create_period(db, data["operated_at"].year, data["operated_at"].month)
    txn = m.Transaction(
        doc_type_code=DOC_KIND[data["kind"]],
        kind=data["kind"],
        status="draft",
        operated_at=data["operated_at"],
        amount=data["amount"],
        currency=data["currency"],
        account_id=data["account_id"],
        account_to_id=data.get("account_to_id"),
        amount_to=data.get("amount_to"),
        category_id=data.get("category_id"),
        counterparty_id=data.get("counterparty_id"),
        description=data.get("description", ""),
        dimensions=data.get("dimensions") or {},
        created_by=user_id,
    )
    db.add(txn)
    db.flush()
    validate_transaction(db, txn)
    return txn


def _freeze_rates(db: Session, txn: m.Transaction) -> None:
    """Захват курса на operated_at и расчёт сумм в базовой валюте (ADR-003)."""
    txn.rate = rate_for(db, txn.operated_at, txn.currency)
    txn.amount_base = compute_base(txn.amount, txn.rate)
    if txn.kind == "transfer":
        txn.rate_to = rate_for(db, txn.operated_at, txn.currency_to)
        if txn.amount_to is None:
            # кросс-валютный перевод без явной суммы зачисления — по курсам
            txn.amount_to = quantize2(txn.amount * txn.rate / txn.rate_to)
        txn.amount_to_base = compute_base(txn.amount_to, txn.rate_to)


def post_transaction(db: Session, txn: m.Transaction) -> m.Transaction:
    if txn.status == "posted":
        raise AccountingError(409, f"Transaction {txn.doc_number or txn.id} is already posted")
    ensure_period_open(db, txn.operated_at)
    validate_transaction(db, txn)
    _freeze_rates(db, txn)
    txn.doc_number = next_doc_number(db, txn.doc_type_code, txn.operated_at)
    txn.status = "posted"
    db.flush()
    events.publish(db, "acc.transaction.posted", posted_payload(txn))
    return txn


def posted_payload(txn: m.Transaction) -> dict[str, Any]:
    """Контракт acc.transaction.posted — поля неприкосновенны (ADR-002); деньги строками."""
    return {
        "transaction_id": str(txn.id),
        "doc_number": txn.doc_number,
        "kind": txn.kind,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "amount_base": str(txn.amount_base),
        "rate": str(txn.rate),
        "operated_at": txn.operated_at.isoformat(),
        "account_id": str(txn.account_id),
        "category_id": str(txn.category_id) if txn.category_id else None,
        "counterparty_id": str(txn.counterparty_id) if txn.counterparty_id else None,
        "dimensions": txn.dimensions or {},
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def update_transaction(
    db: Session, txn: m.Transaction, *, user_id: uuid.UUID, changes: dict
) -> m.Transaction:
    """Правка: черновика — свободно, проведённого — только в открытом периоде;
    каждое изменение пишется в журнал версий, курс/amount_base пересчитываются."""
    ensure_period_open(db, txn.operated_at)
    diff: dict[str, dict[str, Any]] = {}
    for field, new_value in changes.items():
        old_value = getattr(txn, field)
        if old_value != new_value:
            diff[field] = {"old": _serialize(old_value), "new": _serialize(new_value)}
            setattr(txn, field, new_value)
    if not diff:
        return txn
    validate_transaction(db, txn)
    if txn.status == "posted":
        ensure_period_open(db, txn.operated_at)  # новый период тоже должен быть открыт
        _freeze_rates(db, txn)
    db.flush()
    record_version(db, "acc.transaction", str(txn.id), user_id, diff)
    return txn


def create_storno(
    db: Session, txn: m.Transaction, *, user_id: uuid.UUID, reason: str
) -> m.Transaction:
    """Экономическая отмена: парный документ с инвертированным kind (для перевода
    счета меняются местами вместе с суммами), проводится сразу; пара гасится
    в отчётах естественным суммированием."""
    if txn.status != "posted":
        raise AccountingError(422, "Only posted transactions can be stornoed")
    if txn.storno_of_id is not None:
        raise AccountingError(422, "Storno of a storno document is not allowed")
    ensure_period_open(db, txn.operated_at)
    kind = INVERSE_KIND[txn.kind]
    storno = m.Transaction(
        doc_type_code=STORNO_DOC_TYPE,
        kind=kind,
        status="draft",
        operated_at=txn.operated_at,
        account_id=txn.account_to_id if kind == "transfer" else txn.account_id,
        account_to_id=txn.account_id if kind == "transfer" else None,
        amount=txn.amount_to if kind == "transfer" else txn.amount,
        amount_to=txn.amount if kind == "transfer" else None,
        currency=txn.currency_to if kind == "transfer" else txn.currency,
        # статья типизирована по kind: при инверсии income↔expense она не подходит,
        # у перевода kind сохраняется — категория переносится
        category_id=txn.category_id if kind == "transfer" else None,
        counterparty_id=txn.counterparty_id,
        description=f"Сторно {txn.doc_number}",
        dimensions=txn.dimensions or {},
        created_by=user_id,
        storno_of_id=txn.id,
    )
    db.add(storno)
    db.flush()
    post_transaction(db, storno)
    txn.is_stornoed = True
    db.flush()
    events.publish(db, "acc.transaction.stornoed", {
        "transaction_id": str(storno.id),
        "storno_of": str(txn.id),
        "reason": reason,
    })
    record_version(db, "acc.transaction", str(txn.id), user_id,
                   {"is_stornoed": {"old": False, "new": True}}, reason=reason)
    return storno


def mark_deleted(
    db: Session, txn: m.Transaction, *, user_id: uuid.UUID, reason: str
) -> m.Transaction:
    """Метка удаления — только для дублей: скрывает из отчётов, остаётся в БД."""
    if txn.is_deleted:
        raise AccountingError(409, "Transaction is already marked as deleted")
    txn.is_deleted = True
    db.flush()
    record_version(db, "acc.transaction", str(txn.id), user_id,
                   {"is_deleted": {"old": False, "new": True}}, reason=reason)
    return txn


def unmark_deleted(
    db: Session, txn: m.Transaction, *, user_id: uuid.UUID, reason: str | None
) -> m.Transaction:
    if not txn.is_deleted:
        raise AccountingError(409, "Transaction is not marked as deleted")
    txn.is_deleted = False
    db.flush()
    record_version(db, "acc.transaction", str(txn.id), user_id,
                   {"is_deleted": {"old": True, "new": False}}, reason=reason)
    return txn


# ---------- Справочники ----------

def next_counterparty_code(db: Session) -> str:
    number = db.execute(text("SELECT nextval('mgmt_accounting.counterparty_code_seq')")).scalar_one()
    return f"К-{number:05d}"


def create_counterparty(db: Session, data: dict) -> tuple[m.Counterparty, str | None]:
    """Дубль по ИНН+КПП — предупреждение в ответе, не запрет."""
    counterparty = m.Counterparty(
        internal_code=next_counterparty_code(db),
        name=data["name"],
        inn=data.get("inn", ""),
        kpp=data.get("kpp", ""),
        contact_id=data.get("contact_id"),
    )
    db.add(counterparty)
    db.flush()
    warning = None
    if counterparty.inn:
        duplicate = db.scalar(select(m.Counterparty).where(
            m.Counterparty.inn == counterparty.inn,
            m.Counterparty.kpp == counterparty.kpp,
            m.Counterparty.id != counterparty.id,
        ))
        if duplicate is not None:
            warning = (
                f"Possible duplicate: {duplicate.internal_code} «{duplicate.name}» "
                "has the same INN+KPP"
            )
    return counterparty, warning


def patch_account(
    db: Session, account: m.Account, *, user_id: uuid.UUID, changes: dict
) -> m.Account:
    diff: dict[str, dict[str, Any]] = {}
    for field, value in changes.items():
        old_value = getattr(account, field)
        if old_value != value:
            diff[field] = {"old": _serialize(old_value), "new": _serialize(value)}
            setattr(account, field, value)
    if diff:
        record_version(db, "acc.account", str(account.id), user_id, diff)
    return account


# ---------- Курсы (подписчик integration.rates.fetched, этап C) ----------

def upsert_rates_from_event(payload: dict) -> None:
    """Контракт integration.rates.fetched: {date, source, rates: [{currency, rate}]}.

    Деньги строками (ADR-003) → Decimal; upsert по (date, currency),
    внеурочные/дубли идемпотентно перезаписываются; своя сессия.
    """
    day = date.fromisoformat(str(payload["date"]))
    db = SessionLocal()
    try:
        for item in payload.get("rates", []):
            row = db.scalar(select(m.Rate).where(
                m.Rate.date == day, m.Rate.currency == item["currency"]
            ))
            if row is None:
                db.add(m.Rate(date=day, currency=item["currency"],
                              rate=Decimal(str(item["rate"])), source="connector"))
            else:
                row.rate = Decimal(str(item["rate"]))
                row.source = "connector"
        db.commit()
    finally:
        db.close()


# ---------- Отчёты ----------

def _txn_flows(txn: m.Transaction) -> list[tuple[uuid.UUID, Decimal]]:
    """Изменения остатков счетов в базовой валюте: [(счёт, дельта)]."""
    if txn.kind == "income":
        return [(txn.account_id, txn.amount_base)]
    if txn.kind == "expense":
        return [(txn.account_id, -txn.amount_base)]
    return [
        (txn.account_id, -txn.amount_base),
        (txn.account_to_id, txn.amount_to_base),
    ]


def cashflow(
    db: Session, date_from: date, date_to: date, account_id: uuid.UUID | None
) -> dict[str, Any]:
    """Отчёт «движение денег»: opening/closing balance и итоги по категориям.

    Учёт ведётся по operated_at; сторно-пары гасятся суммированием, помеченные
    удалением (is_deleted) исключены.
    """
    query = select(m.Transaction).where(
        m.Transaction.status == "posted",
        m.Transaction.is_deleted.is_(False),
        m.Transaction.operated_at <= date_to,
    )
    if account_id is not None:
        query = query.where(or_(
            m.Transaction.account_id == account_id,
            m.Transaction.account_to_id == account_id,
        ))
    rows = db.scalars(query.order_by(m.Transaction.operated_at)).all()

    opening = Decimal(0)
    totals: dict[tuple[str, uuid.UUID | None], Decimal] = {}
    for txn in rows:
        flows = [
            (account, delta) for account, delta in _txn_flows(txn)
            if account_id is None or account == account_id
        ]
        if txn.operated_at < date_from:
            opening += sum(delta for _, delta in flows)
        else:
            delta = sum(delta for _, delta in flows)
            key = (txn.kind, txn.category_id)
            totals[key] = totals.get(key, Decimal(0)) + delta

    closing = opening + sum(totals.values())
    return {
        "date_from": date_from,
        "date_to": date_to,
        "account_id": account_id,
        "opening_balance": quantize2(opening),
        "closing_balance": quantize2(closing),
        "totals": [
            {"kind": kind, "category_id": category_id, "total": quantize2(total)}
            for (kind, category_id), total in sorted(
                totals.items(), key=lambda item: (item[0][0], str(item[0][1]))
            )
        ],
    }
