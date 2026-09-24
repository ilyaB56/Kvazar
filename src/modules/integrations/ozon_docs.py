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


def create_expense(db, connection, ozon_transaction) -> None:
    """Этап C: транзакция Ozon → расход по статье («Комиссия Ozon» и др.,
    seed-справочник); одна транзакция на операцию (§10.1)."""
    raise NotImplementedError  # этап C
