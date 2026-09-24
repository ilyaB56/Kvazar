"""Создание документов учёта по данным Ozon (ozon-connector-spec §3.3.3–3.3.4).

Паттерн sales-automation: документы — через публичный API служебным
токеном (ai-self-api подключения организации); модуль интеграций не
лезет в схему учёта напрямую. Деньги/количества — Decimal-строки (ADR-003).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.modules.integrations import models as m
from src.modules.integrations.sales_flow import AccountingApi, FlowError

logger = logging.getLogger(__name__)

OZON_COUNTERPARTY = "Ozon"


def _api(db, company_id) -> AccountingApi:
    """Клиент API учёта от имени организации (ai-self-api, X-API-Token)."""
    connection = db.scalar(select(m.Connection).where(
        m.Connection.name == "ai-self-api",
        m.Connection.company_id == company_id,
        m.Connection.is_active.is_(True)))
    if connection is None:
        raise FlowError("no_self_api", "connection ai-self-api not found (seed)")
    from .crypto import decrypt_dict
    from .connectors.builtin import registry

    connector = registry.build(connection.connector_code, connection.config,
                               decrypt_dict(connection.credentials_enc))
    api_key = connector.credentials.get("api_key", "")
    return AccountingApi(
        base_url=str((connection.config or {}).get("base_url", "http://api:8000")),
        api_token=str(api_key))


def _mapping(db, connection, offer_id: str):
    """offer_id → item_mappings строки этой ozon-связи (или глобальная)."""
    mapping = db.scalar(select(m.ItemMapping).where(
        m.ItemMapping.connection_id == connection.id,
        m.ItemMapping.external_item_id == offer_id,
        m.ItemMapping.is_active.is_(True)))
    if mapping is None:
        mapping = db.scalar(select(m.ItemMapping).where(
            m.ItemMapping.connection_id.is_(None),
            m.ItemMapping.external_item_id == offer_id,
            m.ItemMapping.is_active.is_(True)))
    return mapping


def create_sales_order(db, connection, ozon_order) -> tuple[bool, bool]:
    """Этап B: заказ Ozon → sales_orders draft (контрагент «Ozon», строки
    по item_mappings, цены из заказа). (создан, mapping_error).
    Несмапленная позиция → заказ НЕ создаётся, флаг mapping_error (§7.4);
    ретрай после маппинга создаёт заказ."""
    api = _api(db, connection.company_id)
    lines = []
    for line in ozon_order.lines or []:
        mapping = _mapping(db, connection, str(line.get("offer_id", "")))
        if mapping is None:
            return False, True
        price = line.get("price")
        if price in (None, ""):
            return False, True
        lines.append({
            "item_id": str(mapping.item_id),
            "qty": str(line.get("qty", 1)),
            "unit_price": str(price),  # Decimal-строка из заказа (ADR-003)
        })
    if not lines:
        return False, True
    try:
        cp_id = api.find_or_create({"name": OZON_COUNTERPARTY}, "Ozon")
        order = api.create_order(cp_id, lines)
    except FlowError as exc:
        logger.warning("ozon order %s not created: %s",
                       ozon_order.posting_number, exc)
        return False, False
    ozon_order.sales_order_id = order["id"]
    logger.info("ozon posting %s → sales_order %s",
                ozon_order.posting_number, order["id"])
    return True, False


def on_order_cancelled(db, connection, ozon_order) -> None:
    """Отмена на Ozon (§3.3.3): черновик-заказ удаляем; подтверждённый —
    вручную существующими операциями (сторнирование)."""
    if ozon_order.sales_order_id is None:
        return
    api = _api(db, connection.company_id)
    try:
        order = api._call("GET", f"/accounting/sales-orders/{ozon_order.sales_order_id}")
    except FlowError:
        return
    if order.get("status") == "draft":
        try:
            api._call("DELETE", f"/accounting/sales-orders/{ozon_order.sales_order_id}")
            ozon_order.sales_order_id = None
        except FlowError as exc:
            logger.warning("ozon cancel: draft %s delete failed: %s",
                           ozon_order.sales_order_id, exc)


# тип операции Ozon → статья расходов (seed-справочник, §3.3.4)
OZON_CATEGORIES = {
    "commission": "Комиссия Ozon",
    "logistics": "Логистика Ozon",
    "advertising": "Реклама Ozon",
}


def expense_category(operation_type: str) -> str:
    lowered = (operation_type or "").lower()
    if any(k in lowered for k in ("логист", "last mile", "delivery",
                                  "миля", "fulfillment")):
        return OZON_CATEGORIES["logistics"]
    if any(k in lowered for k in ("реклам", "advertis", "promotion", "трафарет")):
        return OZON_CATEGORIES["advertising"]
    # комиссия и прочие удержания (возвраты — приходят с минусом)
    return OZON_CATEGORIES["commission"]


def create_expense(db, connection, ozon_transaction) -> None:
    """Этап C: операция Ozon → расход по статье (контрагент «Ozon»),
    одна транзакция на операцию (§10.1); transfer не проводим (§10.2).

    Закрытый период / ошибка API → FlowError: расход не создан,
    transaction_id остаётся NULL — ретрай на следующем sync (§7.10).
    """
    api = _api(db, connection.company_id)
    category_name = expense_category(ozon_transaction.operation_type)
    category_id = api.find_or_create_category(category_name)
    cp_id = api.find_or_create({"name": OZON_COUNTERPARTY}, "Ozon")
    amount = abs(ozon_transaction.amount)
    if amount == 0:
        ozon_transaction.transaction_id = None
        return
    description = (f"Ozon: {ozon_transaction.operation_type} "
                   f"(операция {ozon_transaction.operation_id})")
    account_id = api.default_account_id()
    txn = api.create_expense(
        counterparty_id=cp_id, category_id=category_id, account_id=account_id,
        amount=f"{amount:.4f}", operated_at=(ozon_transaction.posted_at or "")[:10],
        description=description)
    ozon_transaction.transaction_id = txn["id"]
