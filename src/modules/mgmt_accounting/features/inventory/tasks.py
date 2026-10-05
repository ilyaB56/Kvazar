"""Периодика склада (notifications-spec §6.2): sweep низких остатков.

Страховка событийного канала acc.inventory.low_stock «когда вкладки
закрыты»: раз в час по активным items с порогом. Идемпотентность —
dedup low_stock:{item_id}:{день}.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.core.notifications.service import notify
from src.db import SessionLocal
from src.worker import celery_app

from . import models as m
from .service import on_hand

logger = logging.getLogger(__name__)


@celery_app.task(name="src.modules.mgmt_accounting.features.inventory.tasks.sweep_low_stock")
def sweep_low_stock() -> dict:
    db = SessionLocal()
    created = 0
    try:
        items = db.scalars(select(m.Item).where(
            m.Item.is_active.is_(True),
            m.Item.low_stock_threshold.is_not(None),
        )).all()
        bucket = datetime.now(UTC).strftime("%Y-%m-%d")
        for item in items:
            qty = on_hand(db, item.id)
            if qty > item.low_stock_threshold:
                continue
            created += notify(
                db, company_id=item.company_id, kind="low_stock",
                audience="admins",
                title=f"Низкий остаток: {item.sku}",
                body=f"Остаток {qty} при пороге {item.low_stock_threshold}",
                entity_id=str(item.id),
                dedup_key=f"low_stock:{item.id}:{bucket}",
            )
        db.commit()
    finally:
        db.close()
    logger.info("sweep_low_stock: %d уведомлений", created)
    return {"created": created}
