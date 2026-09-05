"""Telegram-уведомления по событиям шины (showcase-chain, этап D).

Белый список событий; активные правила по событию → рендер шаблона
(подстановка {ключ} из payload, простые ключи верхнего уровня) → push
через коннектор connection «Telegram». Ошибка отправки пишется в журнал
и не валит обработку остальных правил/событий. Сеть — только через
коннектор (ADR-001).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.db import SessionLocal
from src.modules.integrations import models as m
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict

logger = logging.getLogger(__name__)

NOTIFY_EVENTS = (
    "acc.transaction.posted",
    "acc.period.closed",
    "integration.sync.failed",
    "system.updated",
    "system.rollback",
    # витрина CRM (mini-crm-spec, этап A)
    "crm.deal.created",
    "crm.deal.stage_changed",
    "crm.deal.won",
    "crm.deal.lost",
    # складское ядро (resources-core-spec §5, этап A)
    "acc.inventory.stock_changed",
    "acc.inventory.low_stock",
    # закупки (resources-core-spec §5, этап B)
    "acc.purchase.order.created",
    "acc.purchase.order.confirmed",
    "acc.purchase.received",
    # продажи (resources-core-spec §5, этап C)
    "acc.sales.order.created",
    "acc.sales.order.confirmed",
    "acc.sales.shipped",
)


class _SafeDict(dict):
    """Неизвестный ключ шаблона остаётся как есть ({что-то})."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, payload: dict) -> str:
    """Подстановка {ключ} из payload верхнего уровня; чистая функция."""
    values = _SafeDict({k: v for k, v in payload.items() if isinstance(v, (str, int, float))})
    return (template or "").format_map(values)


def send_notification(rule: m.NotificationRule, payload: dict) -> bool:
    """Отправить одно правило через connection «Telegram» (коннектор)."""
    db = SessionLocal()
    try:
        # детерминированно: самый свежий активный telegram-connection
        connection = db.scalar(select(m.Connection).where(
            m.Connection.connector_code == "telegram_bot",
            m.Connection.is_active.is_(True),
        ).order_by(m.Connection.created_at.desc()).limit(1))
        if connection is None:
            logger.warning("notify: нет активного connection telegram_bot — пропуск")
            return False
        connector = connector_registry.build(
            "telegram_bot", connection.config, decrypt_dict(connection.credentials_enc)
        )
        text = render_template(rule.template, payload)
        result = connector.push(payload={"chat_id": rule.chat_id, "text": text})
        if not result.ok:
            logger.warning("notify: отправка не удалась (rule=%s): %s", rule.name, result.error)
        return result.ok
    finally:
        db.close()


def make_notification_handler(event_name: str):
    def handler(payload: dict) -> None:
        # отправка уходит в воркер: блокирующий httpx в api-процессе вешал
        # event loop (dispatch_outbox вызывается и из вебхуков)
        from src.modules.integrations.tasks import notify_task

        db = SessionLocal()
        try:
            rules = db.scalars(select(m.NotificationRule).where(
                m.NotificationRule.event_name == event_name,
                m.NotificationRule.is_active.is_(True),
            )).all()
        finally:
            db.close()
        for rule in rules:
            try:
                notify_task.delay(str(rule.id), dict(payload))
            except Exception:  # noqa: BLE001 — уведомления не ломают диспетчер
                logger.exception("notify: queueing rule %s failed", rule.name)

    return handler


def register_notification_handlers() -> None:
    from src.core import events

    for event_name in NOTIFY_EVENTS:
        events.subscribe(event_name, make_notification_handler(event_name))
