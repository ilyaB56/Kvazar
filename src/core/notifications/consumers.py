"""Потребитель событий шины → in-app уведомления (notifications-spec §6.1).

Best-effort как сама шина: диспетчер помечает событие processed до
обработчиков; исключение здесь логируется и не роняет остальных
подписчиков. Событие без company_id в payload пропускается (WARNING) —
адресовать некому; это же защищает от кросс-тенантной утечки.
Исключение — platform_admins-типы (signup_request): company_id нет,
строка платформенная (NULL).

Этап C (§12-C): единая точка «условие → каналы» — если по (событие ×
организация) есть активные notification_rules и ни в одном нет канала
in_app, in-app-создание глушится (Telegram продолжает работать); без
правил in_app работает по умолчанию.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from sqlalchemy import select

from src.core import events
from src.core.notifications.registry import EVENT_KINDS, REGISTRY
from src.core.notifications.service import notify
from src.db import SessionLocal

logger = logging.getLogger(__name__)


def in_app_allowed(db, event_name: str, company_id) -> bool:
    """Активные правила без in_app-канала глушат in-app для этой пары (§12-C).

    Ленивый импорт: ядро не зависит от модуля integrations на уровне
    импорта (слои ADR-005); таблица правил читается только здесь.
    """
    import uuid

    from src.modules.integrations import models as im

    rules = db.scalars(select(im.NotificationRule).where(
        im.NotificationRule.event_name == event_name,
        im.NotificationRule.is_active.is_(True),
        im.NotificationRule.company_id == uuid.UUID(str(company_id)),
    )).all()
    if not rules:
        return True  # без правил — in_app работает (умолчание, §12-C)
    return any("in_app" in (r.channels or []) for r in rules)


def _make_consumer(event_name: str, kind: str):
    kd = REGISTRY[kind]

    def consumer(payload: dict) -> None:
        company_id = payload.get("company_id")
        # platform_admins-типы адресуются без организации (строка NULL);
        # admins/all без company_id адресоваться некому (§6.1); типы
        # audience=user адресуются по user_id из payload
        if kd.audience in ("admins", "all") and not company_id:
            logger.warning("notifications: событие %s без company_id — пропуск",
                           event_name)
            return
        user_id = None
        if kd.audience == "user":
            user_id = payload.get("user_id")
            if not user_id:
                logger.warning("notifications: событие %s без user_id — пропуск",
                               event_name)
                return
        db = SessionLocal()
        try:
            if company_id and not in_app_allowed(db, event_name, company_id):
                return  # правила заглушили in_app (§12-C)
            # событийные типы: bucket-день — повторные события не плодят
            # строк (§6.5); ключ не включает user_id (он в UNIQUE)
            bucket = datetime.now(UTC).strftime("%Y-%m-%d")
            entity_id = next(
                (str(payload[k]) for k in kd.entity_keys if payload.get(k)), "")
            dedup_key = f"{kind}:{entity_id}:{bucket}" if entity_id else None
            created = notify(
                db, company_id=company_id, kind=kind, audience=kd.audience,
                user_id=user_id,
                title=kd.title(payload), body=kd.body(payload),
                entity_id=entity_id or None, dedup_key=dedup_key,
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
