"""Сервисный слой mini_crm: сделки, move-правила, версии, события.

ADR-003: только Decimal, amount_base = amount × rate на сегодня,
half-up до копеек, курс замораживается при создании/правке amount|currency.
Контрагент — по UUID без FK; имя через публичный API (кэш на запрос),
вызов идёт коннектором http_rest (ADR-001: сеть только в коннекторах).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from src.core import events
from src.core.versioning import record_version
from src.modules.mini_crm import models as m

CENT = Decimal("0.01")


class CrmError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def quantize2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------- Курс (rates учёта через публичный SQL-слой ядра модулей) ----------

def rate_for_today(db: Session, currency: str) -> Decimal:
    """Курс к RUB на сегодня; RUB = 1; нет курса — 422 с подсказкой (как в учёте)."""
    if currency == "RUB":
        return Decimal(1)
    row = db.execute(sql_text(
        "SELECT rate FROM mgmt_accounting.rates WHERE currency = :c AND date = :d"
    ), {"c": currency, "d": date.today()}).scalar_one_or_none()
    if row is None:
        raise CrmError(422, f"No rate for {currency} on {date.today().isoformat()}: "
                            "set it via POST /api/v1/accounting/rates or run «Курсы ЦБ»")
    return row


def recompute_base(db: Session, deal: m.Deal) -> None:
    deal.rate = rate_for_today(db, deal.currency)
    deal.amount_base = quantize2(deal.amount * deal.rate)


# ---------- Контрагент: имя через публичный API, кэш на запрос ----------

def counterparty_names(db: Session, ids: list[uuid.UUID]) -> dict[str, str]:
    """Имена контрагентов одним вызовом (кэш в рамках запроса-вызова).

    Чтение справочника — через коннектор http_rest (connection ai-self-api):
    сеть только в коннекторах (ADR-001), модуль чужую схему не трогает.
    """
    wanted = {str(i) for i in ids if i is not None}
    if not wanted:
        return {}
    from src.modules.integrations import models as im
    from src.modules.integrations.connectors.builtin import registry as connector_registry
    from src.modules.integrations.crypto import decrypt_dict

    connection = db.scalar(select(im.Connection).where(
        im.Connection.name == "ai-self-api",
        im.Connection.is_active.is_(True),
    ))
    if connection is None:
        return {}
    connector = connector_registry.build(connection.connector_code,
                                         connection.config, decrypt_dict(connection.credentials_enc))
    result = connector.fetch("/api/v1/accounting/counterparties")
    if not result.ok or not isinstance(result.data, list):
        return {}
    return {row.get("id"): row.get("name", "") for row in result.data
            if row.get("id") in wanted}


# ---------- Сделки ----------

def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def create_deal(db: Session, *, user_id: uuid.UUID, data: dict) -> m.Deal:
    stage = db.get(m.Stage, data.get("stage_id") or "")
    if stage is None or not stage.is_active:
        raise CrmError(422, "Unknown or inactive stage")
    if stage.is_won or stage.is_lost:
        raise CrmError(422, "Deal cannot start in won/lost stage")
    if data.get("amount") is None or Decimal(str(data["amount"])) <= 0:
        raise CrmError(422, "amount must be positive")

    deal = m.Deal(
        title=data["title"],
        stage_id=stage.id,
        counterparty_id=data.get("counterparty_id"),
        contact_id=data.get("contact_id"),
        responsible_id=data.get("responsible_id"),
        amount=Decimal(str(data["amount"])),
        currency=data.get("currency", "RUB"),
        expected_close_at=data.get("expected_close_at"),
        dimensions=data.get("dimensions") or {},
        created_by=user_id,
    )
    recompute_base(db, deal)
    db.add(deal)
    db.flush()
    events.publish(db, "crm.deal.created", {
        "deal_id": str(deal.id), "title": deal.title, "stage": stage.name,
        "amount": str(deal.amount), "currency": deal.currency,
        "amount_base": str(deal.amount_base), "responsible_id": str(deal.responsible_id) if deal.responsible_id else None,
    })
    return deal


def update_deal(db: Session, deal: m.Deal, *, user_id: uuid.UUID, changes: dict) -> m.Deal:
    diff: dict[str, dict[str, Any]] = {}
    money_touched = False
    for field, value in changes.items():
        old = getattr(deal, field)
        if field == "amount" and value is not None:
            value = Decimal(str(value))
        if old != value:
            diff[field] = {"old": _serialize(old), "new": _serialize(value)}
            setattr(deal, field, value)
            if field in ("amount", "currency"):
                money_touched = True
    if not diff:
        return deal
    if money_touched:
        if deal.amount <= 0:
            raise CrmError(422, "amount must be positive")
        recompute_base(db, deal)
    db.flush()
    record_version(db, "crm.deal", str(deal.id), user_id, diff)
    return deal


def move_deal(db: Session, deal: m.Deal, *, user_id: uuid.UUID, to_stage_id: uuid.UUID) -> m.Deal:
    """Правила: в won/lost — только из открытой стадии; выход из won/lost —
    только в открытую (реанимация); won↔lost напрямую запрещён."""
    target = db.get(m.Stage, to_stage_id)
    if target is None or not target.is_active:
        raise CrmError(422, "Unknown or inactive target stage")
    current = db.get(m.Stage, deal.stage_id)
    if (target.is_won or target.is_lost) and (current.is_won or current.is_lost):
        raise CrmError(422, f"Cannot move from '{current.name}' to '{target.name}': "
                            "reanimate the deal into an open stage first")
    if current.is_won and target.is_lost or current.is_lost and target.is_won:
        raise CrmError(422, "Direct move between won and lost is not allowed")

    deal.stage_id = target.id
    now = datetime.now(UTC)
    if target.is_won:
        deal.won_at = now
        deal.lost_at = None
    elif target.is_lost:
        deal.lost_at = now
        deal.won_at = None
    else:  # реанимация в открытую — сброс отметок
        deal.won_at = None
        deal.lost_at = None
    if not target.is_lost:
        deal.lost_reason = None
    db.flush()
    record_version(db, "crm.deal", str(deal.id), user_id,
                   {"stage": {"old": current.name, "new": target.name}})

    payload_common = {
        "deal_id": str(deal.id), "title": deal.title,
        "from_stage": current.name, "to_stage": target.name,
        "amount": str(deal.amount), "currency": deal.currency,
        "amount_base": str(deal.amount_base),
    }
    events.publish(db, "crm.deal.stage_changed", {
        **payload_common, "is_won": target.is_won, "is_lost": target.is_lost,
    })
    if target.is_won:
        events.publish(db, "crm.deal.won", {
            "deal_id": str(deal.id), "title": deal.title,
            "amount": str(deal.amount), "currency": deal.currency,
            "amount_base": str(deal.amount_base),
            "counterparty_id": str(deal.counterparty_id) if deal.counterparty_id else None,
        })
    if target.is_lost:
        events.publish(db, "crm.deal.lost", {
            "deal_id": str(deal.id), "title": deal.title,
            "reason": deal.lost_reason or "",
        })
    return deal


def mark_deleted(db: Session, deal: m.Deal, *, user_id: uuid.UUID, reason: str) -> m.Deal:
    deal.is_deleted = True
    db.flush()
    record_version(db, "crm.deal", str(deal.id), user_id,
                   {"is_deleted": {"old": False, "new": True}}, reason=reason)
    return deal


# ---------- Отчёт pipeline (этап C) ----------

def pipeline(db: Session, responsible_id: uuid.UUID | None,
             date_from: date | None, date_to: date | None) -> dict:
    """Воронка по открытым стадиям + выиграно/проиграно за период.

    weighted = Σ amount_base × probability/100 (квантование half-up до копеек,
    ADR-003); всё в базовой валюте, деньги строками.
    """
    stages = db.scalars(select(m.Stage).where(
        m.Stage.is_active.is_(True),
        m.Stage.is_won.is_(False),
        m.Stage.is_lost.is_(False),
    ).order_by(m.Stage.position)).all()

    rows = []
    grand_count = 0
    grand_total = Decimal(0)
    grand_weighted = Decimal(0)
    for stage in stages:
        deals = db.scalars(select(m.Deal).where(
            m.Deal.stage_id == stage.id,
            m.Deal.is_deleted.is_(False),
        )).all()
        if responsible_id is not None:
            deals = [d for d in deals if d.responsible_id == responsible_id]
        total = sum((d.amount_base or Decimal(0) for d in deals), Decimal(0))
        probability = stage.probability or 0
        weighted = quantize2(sum(
            ((d.amount_base or Decimal(0)) * probability / Decimal(100) for d in deals),
            Decimal(0)))
        rows.append({
            "stage_id": str(stage.id), "stage": stage.name,
            "probability": probability,
            "count": len(deals),
            "total": quantize2(total),
            "weighted": weighted,
        })
        grand_count += len(deals)
        grand_total += total
        grand_weighted += weighted

    def _period(column):
        query = select(m.Deal).where(m.Deal.is_deleted.is_(False), column.isnot(None))
        if date_from is not None:
            query = query.where(column >= datetime.combine(date_from, datetime.min.time()))
        if date_to is not None:
            query = query.where(column <= datetime.combine(date_to, datetime.max.time()))
        won_lost = db.scalars(query).all()
        if responsible_id is not None:
            won_lost = [d for d in won_lost if d.responsible_id == responsible_id]
        return won_lost

    won_deals = _period(m.Deal.won_at)
    lost_deals = _period(m.Deal.lost_at)
    return {
        "stages": rows,
        "totals": {
            "count": grand_count,
            "total": quantize2(grand_total),
            "weighted": quantize2(grand_weighted),
        },
        "won": {
            "count": len(won_deals),
            "total": quantize2(sum((d.amount_base or Decimal(0) for d in won_deals), Decimal(0))),
        },
        "lost": {"count": len(lost_deals)},
    }
