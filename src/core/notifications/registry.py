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
    # ключи payload, из которых потребитель берёт entity_id/dedup (§6.1);
    # первый непустой выигрывает
    entity_keys: tuple[str, ...] = ()


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
    entity_keys=("job_id",),
))

_def(KindDef(
    kind="low_stock", severity="warning", audience="admins",
    link="/inventory", entity_type="item",
    title=lambda p: f"Низкий остаток: {p.get('sku', 'товар')}",
    body=lambda p: f"Остаток {p.get('qty', '?')} при пороге {p.get('threshold', '?')}",
    entity_keys=("item_id",),
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


# ---------- Этап B (notifications-spec §6.4, §12-B) ----------

_def(KindDef(
    kind="online_order", severity="info", audience="admins",
    link="/crm/orders", entity_type="sales_order",
    title=lambda _p: "Онлайн-заказ оплачен",
    body=lambda p: (f"Платёж {p.get('provider_payment_id') or p.get('payment_id', '')} "
                    f"обработан; заказ {p.get('sales_order_id') or '—'}"),
    entity_keys=("sales_order_id", "payment_id"),
))

_def(KindDef(
    kind="online_payment_failed", severity="critical", audience="admins",
    link="/integrations/payments", entity_type="online_payment",
    title=lambda _p: "Онлайн-платёж требует внимания",
    body=lambda p: f"Причина: {p.get('reason', '—')} (шаг {p.get('step', '—')})",
    entity_keys=("payment_id",),
))

_def(KindDef(
    kind="sales_order_created", severity="info", audience="admins",
    link="/crm/orders", entity_type="sales_order",
    title=lambda p: f"Новый заказ клиента: {p.get('number', '—')}",
    body=lambda p: f"Сумма {p.get('amount', '?')} {p.get('currency', '')}",
    entity_keys=("order_id",),
))

_def(KindDef(
    kind="signup_request", severity="info", audience="platform_admins",
    link="/select-org", entity_type="signup_request",
    title=lambda p: f"Новая заявка на подключение: {p.get('company_name', '—')}",
    body=lambda p: (f"Контакт: {p.get('contact_name') or '—'}, "
                    f"email: {p.get('email', '—')}"),
    entity_keys=("signup_request_id",),
))

_def(KindDef(
    kind="ai_proposal", severity="info", audience="user",
    link="/assistant", entity_type="ai_proposal",
    title=lambda _p: "Предложение ИИ ожидает решения",
    body=lambda p: f"Действие: {p.get('action_type', '—')}",
    entity_keys=("proposal_id",),
))

_def(KindDef(
    kind="crm_task_overdue", severity="warning", audience="user",
    link="/crm/deals", entity_type="crm_task",
    title=lambda p: f"Просрочена задача: {p.get('title', '—')}",
    body=lambda p: f"Срок: {p.get('due_at', '—')}",
))

_def(KindDef(
    kind="deal_overdue", severity="warning", audience="user",
    link="/crm/deals", entity_type="crm_deal",
    title=lambda p: f"Просрочена сделка: {p.get('title', '—')}",
    body=lambda p: f"Ожидаемое закрытие: {p.get('expected_close_at', '—')}",
))


# Событие шины → тип уведомления (потребитель §6.1).
EVENT_KINDS: dict[str, str] = {
    "integration.sync.failed": "sync_failed",
    "acc.inventory.low_stock": "low_stock",
    # этап B (§12-B)
    "integration.payment.processed": "online_order",
    "integration.payment.failed": "online_payment_failed",
    "acc.sales.order.created": "sales_order_created",
    "platform.signup.verified": "signup_request",
    "ai.proposal.created": "ai_proposal",
}
