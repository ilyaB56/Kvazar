"""Потребитель событий шины → in-app уведомления (notifications-spec §6.1).

Best-effort как сама шина: диспетчер помечает событие processed до
обработчиков; исключение здесь логируется и не роняет остальных
подписчиков. Событие без company_id в payload пропускается (WARNING) —
адресовать некому; это же защищает от кросс-тенантной утечки.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from src.core import events
from src.core.notifications.registry import EVENT_KINDS, REGISTRY
from src.core.notifications.service import notify
from src.db import SessionLocal

logger = logging.getLogger(__name__)


def _make_consumer(event_name: str, kind: str):
    kd = REGISTRY[kind]

    def consumer(payload: dict) -> None:
        company_id = payload.get("company_id")
        if not company_id:
            logger.warning("notifications: событие %s без company_id — пропуск",
                           event_name)
            return
        # событийные типы: bucket-день — повторные движения не плодят строк (§6.5)
        bucket = datetime.now(UTC).strftime("%Y-%m-%d")
        entity_id = payload.get("item_id") or payload.get("job_id") or ""
        dedup_key = f"{kind}:{entity_id}:{bucket}" if entity_id else None
        db = SessionLocal()
        try:
            created = notify(
                db, company_id=company_id, kind=kind, audience=kd.audience,
                title=kd.title(payload), body=kd.body(payload),
                entity_id=str(entity_id) or None, dedup_key=dedup_key,
            )
            if created:
                db.commit()
                logger.info("notifications: %s → %d строк (event %s)",
                            kind, created, event_name)
        finally:
            db.close()

    return consumer


def register_notification_consumers() -> None:
    """Подписать in-app-потребителей на события реестра.

    Вызывается из register_event_handlers() — потребитель живёт и в api,
    и в worker (иначе диспетчер пометит события processed без доставки).
    """
    for event_name, kind in EVENT_KINDS.items():
        events.subscribe(event_name, _make_consumer(event_name, kind))
