"""Периодика CRM (notifications-spec §6.2, этап B): sweep просрочек.

Сделки с expected_close_at в прошлом в незакрытой стадии (не won/lost) →
deal_overdue ответственному; задачи (activities) open с due_at в прошлом
→ crm_task_overdue ответственному сделки. Идемпотентность — дневной
dedup_key (UNIQUE user_id+dedup_key): повторные запуски в тот же день
не плодят дублей.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.core.notifications.service import notify
from src.db import SessionLocal
from src.worker import celery_app

from . import models as m

logger = logging.getLogger(__name__)


@celery_app.task(name="src.modules.mini_crm.tasks.sweep_crm_overdue")
def sweep_crm_overdue() -> dict:
    db = SessionLocal()
    created = 0
    try:
        today = datetime.now(UTC).date()
        bucket = today.strftime("%Y-%m-%d")

        # просроченные открытые сделки (expected_close_at в прошлом,
        # стадия не won/lost, не удалена; §12-B)
        deals = db.scalars(
            select(m.Deal).join(m.Stage, m.Deal.stage_id == m.Stage.id).where(
                m.Deal.expected_close_at.is_not(None),
                m.Deal.expected_close_at < today,
                m.Deal.is_deleted.is_(False),
                m.Deal.responsible_id.is_not(None),
                m.Stage.is_won.is_(False),
                m.Stage.is_lost.is_(False),
            )).all()
        for deal in deals:
            created += notify(
                db, company_id=deal.company_id, kind="deal_overdue",
                audience="user", user_id=deal.responsible_id,
                title=f"Просрочена сделка: {deal.title}",
                body=f"Ожидаемое закрытие: {deal.expected_close_at.isoformat()}",
                entity_id=str(deal.id),
                dedup_key=f"crm_deal:{deal.id}:{bucket}",
            )

        # просроченные открытые задачи → ответственному сделки
        # (у activity своего ответственного нет, §6.2)
        activities = db.scalars(
            select(m.Activity).join(m.Deal, m.Activity.deal_id == m.Deal.id).where(
                m.Activity.done.is_(False),
                m.Activity.due_at < today,
                m.Deal.is_deleted.is_(False),
                m.Deal.responsible_id.is_not(None),
            )).all()
        for act in activities:
            deal = db.get(m.Deal, act.deal_id)
            created += notify(
                db, company_id=deal.company_id, kind="crm_task_overdue",
                audience="user", user_id=deal.responsible_id,
                title=f"Просрочена задача: {act.title}",
                body=f"Срок: {act.due_at.isoformat()} (сделка «{deal.title}»)",
                entity_id=str(act.id),
                dedup_key=f"crm_task:{act.id}:{bucket}",
            )
        db.commit()
    finally:
        db.close()
    logger.info("sweep_crm_overdue: %d уведомлений", created)
    return {"created": created}
