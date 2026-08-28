"""Инструменты агента (этап D): whitelist read-only + единственный write
через предложения (propose_transaction — этап E). Вызовы идут в публичный
API учёта через connection ai-self-api с X-API-Token (ADR-006 п.4);
деньги — строки (ADR-003). Сеть — только через коннектор (ADR-001).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.db import SessionLocal
from src.modules.integrations import models as im
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict

logger = logging.getLogger(__name__)

SELF_API_CONNECTION = "ai-self-api"

# JSON-schema инструментов — в системный промпт (формат мока — JSON-протокол)
TOOL_SCHEMAS = """Доступные инструменты. Чтобы вызвать инструмент, ответь СТРОГО одним JSON-объектом:
{"tool": "<имя>", "args": {<аргументы>}}
Если инструмент не нужен — отвечай обычным текстом.

1. get_cashflow: отчёт «движение денег» в базовой валюте.
   args: {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "account_id": "<uuid, необязательно>"}
   Возвращает: opening_balance, closing_balance, totals по категориям (суммы-строки).

2. search_transactions: поиск транзакций.
   args: {"date_from": "YYYY-MM-DD?", "date_to": "YYYY-MM-DD?", "account_id": "<uuid>?",
          "category_id": "<uuid>?", "q": "<текст описания>?", "limit": 10}

3. get_rate: курс валюты к RUB на дату.
   args: {"currency": "USD", "date": "YYYY-MM-DD"}
   Возвращает rate строкой.

4. search_documents: поиск по документам компании.
   args: {"q": "<запрос>", "limit": 5}"""


def _self_api_connector():
    db = SessionLocal()
    try:
        connection = db.scalar(select(im.Connection).where(
            im.Connection.name == SELF_API_CONNECTION,
            im.Connection.is_active.is_(True),
        ))
        if connection is None:
            raise RuntimeError("connection ai-self-api not found (run seed)")
        return connector_registry.build(
            connection.connector_code, connection.config,
            decrypt_dict(connection.credentials_enc),
        )
    finally:
        db.close()


def _api_get(path: str) -> Any:
    connector = _self_api_connector()
    result = connector.fetch(path)
    if not result.ok:
        return {"error": result.error}
    return result.data


def _api_post(path: str, payload: dict) -> Any:
    connector = _self_api_connector()
    result = connector.push(path, payload)
    if not result.ok:
        return {"error": result.error}
    return result.data


# ---------- Whitelist инструментов: всё чтение (ADR-006) ----------

def get_cashflow(date_from: str, date_to: str, account_id: str | None = None) -> Any:
    path = f"/api/v1/accounting/report/cashflow?date_from={date_from}&date_to={date_to}"
    if account_id:
        path += f"&account_id={account_id}"
    return _api_get(path)


def search_transactions(date_from: str | None = None, date_to: str | None = None,
                        account_id: str | None = None, category_id: str | None = None,
                        q: str | None = None, limit: int = 10) -> Any:
    params = [f"limit={limit}"]
    if date_from:
        params.append(f"date_from={date_from}")
    if date_to:
        params.append(f"date_to={date_to}")
    if account_id:
        params.append(f"account_id={account_id}")
    if category_id:
        params.append(f"category_id={category_id}")
    return _api_get("/api/v1/accounting/transactions?" + "&".join(params))


def get_rate(currency: str, date: str) -> Any:
    return _api_get(f"/api/v1/accounting/rates?currency={currency}&date_from={date}&date_to={date}")


def search_documents(q: str, limit: int = 5) -> Any:
    from urllib.parse import quote

    return _api_get(f"/api/v1/ai/search?q={quote(q)}&limit={limit}")


TOOLS: dict[str, Any] = {
    "get_cashflow": get_cashflow,
    "search_transactions": search_transactions,
    "get_rate": get_rate,
    "search_documents": search_documents,
}


def run_tool(name: str, args: dict) -> Any:
    """Выполнить инструмент из whitelist; неизвестный — ошибка (не исполнение)."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return tool(**(args or {}))
    except TypeError as exc:
        return {"error": f"bad args for {name}: {exc}"}
