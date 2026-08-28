"""Классификация входящих выписок в предложения (этап F).

Подписчик на integration.data.fetched: items данных fetch-джобы → в фоновой
Celery-задаче (не в request-цикле) распознать сумму/дату/описание → подобрать
статью и счёт → proposal create_transaction с reason «из выписки {job}».
Идемпотентность — хэш item+job. Всегда подтверждение (даже при autopapply —
деньги из внешнего источника, ADR-006 п.5). Ошибки — в журнал, диспетчер
не валить.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import date

from src.db import SessionLocal
from src.modules.ai_agent.proposals import create_proposal

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_EVENT = 20
AMOUNT_PATTERN = re.compile(r"(\d[\d\s]*(?:[.,]\d{1,2})?)")
DATE_PATTERNS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")


def item_idempotency_key(job: str, item: dict) -> str:
    """Стабильный хэш item+job — дубли событий не создают предложений дважды."""
    canonical = json.dumps({"job": job, "item": item}, sort_keys=True,
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:64]


def parse_amount(text: str) -> str | None:
    match = AMOUNT_PATTERN.search(text or "")
    if match is None:
        return None
    raw = match.group(1).replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return f"{value:.2f}"


def parse_date(*values) -> str:
    for value in values:
        if not value:
            continue
        text = str(value)[:10]
        for pattern in DATE_PATTERNS:
            try:
                from datetime import datetime as dt

                return dt.strptime(text, pattern).date().isoformat()
            except ValueError:
                continue
    return date.today().isoformat()


def guess_kind(text: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("поступлен", "приход", "перевод от", "зачислен", "оплата от")):
        return "income"
    if any(word in lowered for word in ("списан", "платёж", "оплата ", "покупка", "снятие")):
        return "expense"
    return "income"


def classify_item(job: str, item: dict, user_id: uuid.UUID,
                  default_account_id: str | None) -> dict | None:
    """Распознать платёж и создать proposal (или None, если нет суммы).

    Деньги-строки (ADR-003); статьи/счёт — по справочникам через поиск
    по имени в описании; предложению всегда нужно подтверждение.
    """
    text = " ".join(str(item.get(key, "")) for key in ("description", "назначение",
                                                       "purpose", "note", "comment"))
    amount = item.get("amount") or parse_amount(text)
    if amount is None:
        logger.info("classify: item without amount skipped (job=%s)", job)
        return None
    amount = str(amount).replace(" ", "").replace(",", ".")
    try:
        amount = f"{float(amount):.2f}"
    except ValueError:
        logger.info("classify: unparsable amount %r (job=%s)", amount, job)
        return None
    operated_at = parse_date(item.get("date"), item.get("operated_at"), item.get("дата"))
    kind = item.get("kind") or guess_kind(text)
    account_id = item.get("account_id") or default_account_id
    if not account_id:
        logger.warning("classify: no account for proposal (job=%s)", job)
        return None

    proposal = create_proposal(
        user_id=user_id,
        action_type="create_transaction",
        payload={
            "kind": kind, "operated_at": operated_at, "amount": str(amount),
            "currency": item.get("currency", "RUB"), "account_id": account_id,
            "description": text[:200],
        },
        reason=f"из выписки {job}",
        idempotency_key=item_idempotency_key(job, item),
        respect_autopapply=False,  # деньги извне — всегда подтверждение (ADR-006)
    )
    return {"proposal_id": str(proposal.id), "status": proposal.status}


def on_integration_data_fetched(payload: dict) -> None:
    """Подписчик: разложить в фоновую задачу (классификация не в диспетчере)."""
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return
    from src.modules.ai_agent.tasks import classify_task

    try:
        classify_task.delay(dict(payload))
    except Exception:  # noqa: BLE001 — подписчик не валит диспетчер
        logger.exception("classify: queueing failed")


def run_classify(payload: dict) -> dict:
    """Celery-задача: до 20 items за событие; ошибки — в журнал."""
    from sqlalchemy import select

    from src.core.models import User

    job = str(payload.get("job", "unknown"))
    items = payload.get("items") or []
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.role == "admin",
                                             User.is_active.is_(True)))
        user_id = admin.id if admin else None
        if user_id is None:
            logger.warning("classify: нет активного админа — предложения некому адресовать")
            return {"skipped": True}
        account_id = payload.get("account_id")
    finally:
        db.close()

    created = []
    for item in items[:MAX_ITEMS_PER_EVENT]:
        if not isinstance(item, dict):
            continue
        try:
            result = classify_item(job, item, user_id, account_id)
            if result:
                created.append(result)
        except Exception:  # noqa: BLE001 — один item не валит остальные
            logger.exception("classify: item failed (job=%s)", job)
    logger.info("classify: %d proposals from job %s", len(created), job)
    return {"created": created}
