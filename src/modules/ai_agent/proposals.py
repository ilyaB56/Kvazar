"""Предложения записи (этап E; ADR-006 п.5: запись — через подтверждение).

Создание proposal (из чата через write-инструмент propose_transaction или
из этапа F по выпискам), approve/reject, автоприменение по настройке
пользователя. Применение вызывает публичный API учёта токеном агента.
События ai.proposal.* — без сумм (суммы в proposal, ADR-002/006).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from src.core import events
from src.db import SessionLocal
from src.modules.ai_agent import models as m

ACTION_TYPES = ("create_transaction", "categorize")


def _publish(db, name: str, proposal: m.Proposal) -> None:
    payload = {
        "proposal_id": str(proposal.id),
        "action_type": proposal.action_type,
        "company_id": str(proposal.company_id),
    }
    events.publish(db, name, payload)
    # ключевое действие агента — в events_log (ADR-006 п.7), без сумм
    from src.core.models import AuditEvent

    db.add(AuditEvent(user_id=proposal.user_id, action=name,
                      entity_type="ai_proposal", entity_id=str(proposal.id),
                      payload=payload))


def get_autopapply(db, user_id: uuid.UUID) -> bool:
    row = db.get(m.UserSettings, user_id)
    return bool(row and row.autopapply)


def set_autopapply(db, user_id: uuid.UUID, value: bool) -> m.UserSettings:
    row = db.get(m.UserSettings, user_id)
    if row is None:
        row = m.UserSettings(user_id=user_id, autopapply=value)
        db.add(row)
    else:
        row.autopapply = value
    return row


def create_proposal(*, user_id: uuid.UUID, action_type: str, payload: dict,
                    reason: str = "", idempotency_key: str | None = None,
                    respect_autopapply: bool = True,
                    company_id: uuid.UUID | None = None) -> m.Proposal:
    """Создать предложение; при autopapply=true — применить сразу (auto_applied).

    idempotency_key (этап F): существующий pending-предложение с тем же ключом
    не дублируется.
    """
    db = SessionLocal()
    try:
        if idempotency_key:
            existing = db.scalar(select(m.Proposal).where(
                m.Proposal.idempotency_key == idempotency_key))
            if existing is not None:
                return existing

        proposal = m.Proposal(
            user_id=user_id, action_type=action_type, payload=payload,
            reason=reason, status="pending", idempotency_key=idempotency_key,
            company_id=company_id,
        )
        db.add(proposal)
        db.flush()
        _publish(db, "ai.proposal.created", proposal)

        auto = respect_autopapply and get_autopapply(db, user_id)
        if auto:
            db.commit()
            apply_proposal(proposal.id, decided_by=user_id, auto=True)
            db.expire_all()
            return db.get(m.Proposal, proposal.id)
        db.commit()
        return proposal
    finally:
        db.close()


def apply_proposal(proposal_id, *, decided_by: uuid.UUID, auto: bool = False) -> m.Proposal:
    """Применить: создать транзакцию в учёте через API токеном агента."""
    from src.modules.ai_agent.tools import _api_post

    db = SessionLocal()
    try:
        proposal = db.get(m.Proposal, proposal_id)
        if proposal is None:
            raise ValueError("proposal not found")
        if proposal.status != "pending":
            raise ValueError(f"proposal is {proposal.status}, not pending")

        status = "auto_applied" if auto else "approved"
        try:
            if proposal.action_type == "create_transaction":
                body = dict(proposal.payload)
                body.setdefault("post_immediately", True)
                created = _api_post("/api/v1/accounting/transactions", body)
                if not isinstance(created, dict) or "id" not in created:
                    raise RuntimeError(f"accounting API error: {str(created)[:300]}")
                proposal.result = {"transaction_id": created["id"],
                                   "doc_number": created.get("doc_number")}
            else:
                proposal.result = {"applied": True}
        except Exception as exc:  # noqa: BLE001 — ошибка применения = failed + текст
            proposal.status = "failed"
            proposal.result = {"error": str(exc)[:500]}
            proposal.decided_at = datetime.now(UTC)
            proposal.decided_by = decided_by
            _publish(db, "ai.proposal.failed", proposal)
            db.commit()
            return proposal

        proposal.status = status
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = decided_by
        _publish(db, f"ai.proposal.{status}", proposal)
        db.commit()
        return proposal
    finally:
        db.close()


def reject_proposal(proposal_id, *, decided_by: uuid.UUID) -> m.Proposal:
    db = SessionLocal()
    try:
        proposal = db.get(m.Proposal, proposal_id)
        if proposal is None:
            raise ValueError("proposal not found")
        if proposal.status != "pending":
            raise ValueError(f"proposal is {proposal.status}, not pending")
        proposal.status = "rejected"
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = decided_by
        _publish(db, "ai.proposal.rejected", proposal)
        db.commit()
        return proposal
    finally:
        db.close()
