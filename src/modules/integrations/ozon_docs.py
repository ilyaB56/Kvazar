"""Создание документов учёта по данным Ozon (этапы B/C, §3.3.3–3.3.4).

Паттерн sales-automation: документы создаются через публичный API
служебным токеном (ai-self-api подключения организации) — модуль
интеграций не лезет в схему учёта напрямую. Заглушки этапа A
заменяются реализацией в этапах B (заказы) и C (комиссии).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def create_sales_order(db, connection, ozon_order) -> tuple[bool, bool]:
    """Этап B: ozon_order → sales_orders draft (контрагент «Ozon»,
    строки по item_mappings). Возвращает (создан, mapping_error)."""
    logger.info("ozon_docs.create_sales_order: этап B (пока no-op), posting=%s",
                ozon_order.posting_number)
    return False, False


def on_order_cancelled(db, connection, ozon_order) -> None:
    """Этап B: отмена на Ozon → удалить черновик (§3.3.3)."""
    logger.info("ozon_docs.on_order_cancelled: этап B (пока no-op), posting=%s",
                ozon_order.posting_number)


def create_expense(db, connection, ozon_transaction) -> None:
    """Этап C: транзакция Ozon → расход по статье («Комиссия Ozon» и др.)."""
    logger.info("ozon_docs.create_expense: этап C (пока no-op), op=%s",
                ozon_transaction.operation_id)
