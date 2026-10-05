"""Реестр типов уведомлений (notifications-spec §6.4).

Расширение — правка кода с ревью (как NOTIFY_EVENTS у Telegram-канала).
Каждый тип: severity по умолчанию, аудитория, ссылка, тип сущности и
сборщики текста (title/body) из payload события.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class KindDef:
    kind: str
    severity: str  # info | warning | critical
    audience: str  # user | admins | all | platform_admins
    link: str
    entity_type: str | None
    title: Callable[[dict[str, Any]], str]
    body: Callable[[dict[str, Any]], str] = lambda _p: ""  # noqa: E731


REGISTRY: dict[str, KindDef] = {}


def _def(k: KindDef) -> KindDef:
    REGISTRY[k.kind] = k
    return k


# ---------- Этап A ----------

_def(KindDef(
    kind="sync_failed", severity="critical", audience="admins",
    link="/integrations/sync", entity_type="sync_job",
    title=lambda p: f"Синхронизация упала: {p.get('job', 'задача')}",
    body=lambda p: f"Ошибка: {p.get('error', '')}"[:500],
))

_def(KindDef(
    kind="low_stock", severity="warning", audience="admins",
    link="/inventory", entity_type="item",
    title=lambda p: f"Низкий остаток: {p.get('sku', 'товар')}",
    body=lambda p: f"Остаток {p.get('qty', '?')} при пороге {p.get('threshold', '?')}",
))

_def(KindDef(
    kind="backup_stale", severity="warning", audience="platform_admins",
    link="/settings/system", entity_type="backup",
    title=lambda _p: "Нет свежего бэкапа установки",
    body=lambda p: p.get("detail", "Последний успешный бэкап старше 25 часов"),
))

_def(KindDef(
    kind="totp_deadline", severity="warning", audience="user",
    link="/settings/security", entity_type="user",
    title=lambda p: p.get("title", "Настройте двухфакторную аутентификацию"),
    body=lambda p: p.get("body", ""),
))


# Событие шины → тип уведомления (потребитель §6.1). Этап A — пилотные
# события; B добавит online_order/payment/sales/signup/ai.
EVENT_KINDS: dict[str, str] = {
    "integration.sync.failed": "sync_failed",
    "acc.inventory.low_stock": "low_stock",
}
