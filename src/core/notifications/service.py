"""Хелпер ядра notify() — публичный контракт для модулей (§6.3).

Как record_version: вызывающий передаёт свою Session, notify() только
пишет строки (без коммита — коммит на вызывающем). Идемпотентность:
INSERT … ON CONFLICT (user_id, dedup_key) DO NOTHING.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.models import Notification, User
from src.core.notifications.registry import REGISTRY

logger = logging.getLogger(__name__)


def _resolve_recipients(db: Session, *, company_id: uuid.UUID | None,
                        audience: str, user_id: uuid.UUID | None) -> list[uuid.UUID]:
    """Активные получатели по аудитории (fan-out при создании, §5.2)."""
    if audience == "user":
        if user_id is None:
            raise ValueError("audience='user' требует user_id")
        row = db.get(User, user_id)
        if row is None or not row.is_active:
            return []
        return [user_id]
    conds = [User.is_active.is_(True)]
    if audience == "admins":
        conds += [User.company_id == company_id, User.role == "admin"]
    elif audience == "all":
        conds.append(User.company_id == company_id)
    elif audience == "platform_admins":
        conds.append(User.is_platform_admin.is_(True))
    else:
        raise ValueError(f"unknown audience: {audience}")
    return list(db.scalars(select(User.id).where(*conds)).all())


def notify(db: Session, *, company_id: uuid.UUID | str | None, kind: str,
           audience: str = "admins", user_id: uuid.UUID | str | None = None,
           title: str, body: str = "", entity_type: str | None = None,
           entity_id: str | None = None, dedup_key: str | None = None,
           severity: str | None = None) -> int:
    """Создать уведомления получателям аудитории; вернуть число созданных строк.

    Не коммитит (коммит на вызывающем). dedup_key None → вставка всегда.
    """
    if company_id is not None:
        company_id = uuid.UUID(str(company_id))
    if user_id is not None:
        user_id = uuid.UUID(str(user_id))
    kd = REGISTRY.get(kind)
    if severity is None:
        severity = kd.severity if kd else "info"
    if entity_type is None and kd:
        entity_type = kd.entity_type

    created = 0
    for uid in _resolve_recipients(db, company_id=company_id,
                                   audience=audience, user_id=user_id):
        stmt = pg_insert(Notification).values(
            company_id=company_id, user_id=uid, audience=audience, kind=kind,
            severity=severity, title=title[:255], body=body or "",
            entity_type=entity_type, entity_id=entity_id,
            link=(kd.link if kd else ""),
            dedup_key=dedup_key,
        )
        if dedup_key is not None:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["user_id", "dedup_key"],
                index_where=Notification.dedup_key.is_not(None))
        result = db.execute(stmt)
        created += result.rowcount or 0
    return created
