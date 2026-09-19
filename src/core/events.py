"""Событийная шина ядра.

Правило: модули общаются между собой только через события (и публичный API),
не через прямые вызовы. Публикация — через outbox в той же транзакции;
dispatch_outbox() вызывается после коммита (в request-финализаторе или воркером).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import EventOutbox

logger = logging.getLogger(__name__)

# Локальный реестр обработчиков: имя события -> список функций.
_handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)


def on(event_name: str) -> Callable:
    """Декоратор подписки на событие: @events.on("payment.created")."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _handlers[event_name].append(fn)
        return fn

    return decorator


def subscribe(event_name: str, fn: Callable[..., Any]) -> None:
    _handlers[event_name].append(fn)


def publish(db: Session, event_name: str, payload: dict | None = None) -> None:
    """Записать событие в outbox (внутри текущей транзакции)."""
    db.add(EventOutbox(event_name=event_name, payload=payload or {}))


def dispatch_outbox(db: Session, limit: int = 100) -> int:
    """Забрать непрочитанные события из outbox и отдать подписчикам.

    Возвращает количество обработанных событий. Для продакшена — вынести в
    отдельный воркер; сейчас вызывается после коммита запроса.
    """
    # SKIP LOCKED: dispatch вызывается и в запросах API, и в beat — без
    # блокировки два диспетчера забирают одни строки и дублируют задачи
    # подписчикам (двойные флоу платежа, дубли документов)
    rows = db.execute(
        select(EventOutbox).where(EventOutbox.processed.is_(False))
        .order_by(EventOutbox.id).limit(limit).with_for_update(skip_locked=True)
    ).scalars().all()
    if not rows:
        return 0
    payloads = [(row.event_name, row.payload) for row in rows]
    for row in rows:
        row.processed = True
    db.commit()  # сразу метим обработанными — дубли исключены
    count = 0
    for event_name, payload in payloads:
        for fn in _handlers.get(event_name, []):
            try:
                fn(payload)
            except Exception:  # noqa: BLE001 — один сбой не должен ронять остальные события
                logger.exception("handler %s failed for event %s", fn, event_name)
        count += 1
    return count


def registered_events() -> list[str]:
    return sorted(_handlers.keys())
