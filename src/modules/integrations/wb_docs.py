"""Создание документов учёта по данным WB (этапы B/C, §3.3.3–3.3.4).

Паттерн ozon_docs: документы — через публичный API служебным токеном
(ai-self-api подключения организации). Отличия: контрагент «WB»,
маппинг по vendor_code (external_item_id), статьи «WB: …»
(комиссия/логистика/хранение/штрафы/налог + fallback «WB: Прочее»).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.modules.integrations import models as m
from src.modules.integrations.sales_flow import AccountingApi, FlowError

logger = logging.getLogger(__name__)

WB_COUNTERPARTY = "WB"

# тип операции WB → статья расходов (seed-справочник, §3.1.4 + §10.5)
WB_CATEGORIES = {
    "commission": "Комиссия WB",
    "logistics": "Логистика WB",
    "storage": "Хранение WB",
    "penalty": "Штрафы WB",
    "tax": "Налог WB",
    "other": "WB: Прочее",
}


def expense_category(operation_type: str) -> str:
    lowered = (operation_type or "").lower()
    if any(k in lowered for k in ("комисси", "commission")):
        return WB_CATEGORIES["commission"]
    if any(k in lowered for k in ("логист", "доставк", "logistic", "delivery",
                                  "миля", "paid storage")):
        return WB_CATEGORIES["logistics"]
    if any(k in lowered for k in ("хранен", "storage")):
        return WB_CATEGORIES["storage"]
    if any(k in lowered for k in ("штраф", "penalt", "компенсац")):
        return WB_CATEGORIES["penalty"]
    if any(k in lowered for k in ("налог", "tax", "ндс")):
        return WB_CATEGORIES["tax"]
    return WB_CATEGORIES["other"]


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


def _mapping(db, connection, vendor_code: str):
    """vendor_code → item_mappings строки этой wb-связи (или глобальная).
    Ключ маппинга WB — vendor_code (§10.2)."""
    if not vendor_code:
        return None
    mapping = db.scalar(select(m.ItemMapping).where(
        m.ItemMapping.connection_id == connection.id,
        m.ItemMapping.external_item_id == vendor_code,
        m.ItemMapping.is_active.is_(True)))
    if mapping is None:
        mapping = db.scalar(select(m.ItemMapping).where(
            m.ItemMapping.connection_id.is_(None),
            m.ItemMapping.external_item_id == vendor_code,
            m.ItemMapping.is_active.is_(True)))
    return mapping


def create_sales_order(db, connection, wb_order) -> tuple[bool, bool]:
    """Этап B: заказ WB → sales_orders draft (контрагент «WB», строки по
    item_mappings через vendor_code, цены из заказа Decimal-строками).
    (создан, mapping_error); ретрай после маппинга создаёт заказ."""
    api = _api(db, connection.company_id)
    lines = []
    for line in wb_order.lines or []:
        mapping = _mapping(db, connection, str(line.get("vendor_code", "")))
        if mapping is None:
            return False, True
        price = line.get("price")
        if price in (None, ""):
            return False, True
        lines.append({
            "item_id": str(mapping.item_id),
            "qty": str(line.get("qty", 1)),
            "unit_price": str(price),
        })
    if not lines:
        return False, True
    try:
        cp_id = api.find_or_create({"name": WB_COUNTERPARTY}, "WB")
        order = api.create_order(cp_id, lines)
    except FlowError as exc:
        logger.warning("wb order %s not created: %s", wb_order.uid, exc)
        return False, False
    wb_order.sales_order_id = order["id"]
    logger.info("wb uid %s → sales_order %s", wb_order.uid, order["id"])
    return True, False


def on_order_cancelled(db, connection, wb_order) -> None:
    """Отмена на WB (§3.3.3): черновик-заказ удаляем; подтверждённый —
    вручную существующими операциями (сторнирование)."""
    if wb_order.sales_order_id is None:
        return
    api = _api(db, connection.company_id)
    try:
        order = api._call("GET", f"/accounting/sales-orders/{wb_order.sales_order_id}")
    except FlowError:
        return
    if order.get("status") == "draft":
        try:
            api._call("DELETE", f"/accounting/sales-orders/{wb_order.sales_order_id}")
            wb_order.sales_order_id = None
        except FlowError as exc:
            logger.warning("wb cancel: draft %s delete failed: %s",
                           wb_order.sales_order_id, exc)


def create_expense(db, connection, wb_transaction) -> None:
    """Этап C: операция WB → расход по статье («WB: …», включая fallback
    «WB: Прочее» — §10.5), контрагент «WB», одна транзакция на операцию
    (§10.1); payment (выплата) не проводим — куратор не вызывает.

    Закрытый период / ошибка API → FlowError: расход не создан,
    transaction_id NULL — ретрай на следующем sync (§7.11)."""
    api = _api(db, connection.company_id)
    category_name = expense_category(wb_transaction.operation_type)
    category_id = api.find_or_create_category(category_name)
    cp_id = api.find_or_create({"name": WB_COUNTERPARTY}, "WB")
    amount = abs(wb_transaction.amount)
    if amount == 0:
        wb_transaction.transaction_id = None
        return
    description = (f"WB: {wb_transaction.operation_type} "
                   f"(операция {wb_transaction.operation_id})")
    txn = api.create_expense(
        counterparty_id=cp_id, category_id=category_id,
        account_id=api.default_account_id(),
        amount=f"{amount:.4f}",
        operated_at=(wb_transaction.posted_at or "")[:10],
        description=description)
    wb_transaction.transaction_id = txn["id"]
