"""Коннектор Wildberries Seller API (wb-connector-spec, этап A).

Паттерн OzonSellerConnector; отличия WB (§3.1): аутентификация — один
API-Token в заголовке Authorization (БЕЗ Bearer); rate limit 100/мин —
троттлинг в коннекторе; склады — config.warehouses (WB warehouseID →
наша локация) для push остатков. Сеть — только здесь (ADR-001):
guarded-запросы + allowlist seller-api.wildberries.ru. Остатки WB —
чужой склад: снапшот, НЕ создают movements в ERP.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from src.modules.integrations.connectors.egress import (
    EgressBlocked, guarded_request, log_egress,
)
from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry

logger = logging.getLogger(__name__)

# WB: 100 запросов/мин — троттлим свои вызовы с запасом (§3.1.6)
RATE_LIMIT_PER_MINUTE = 100
_MIN_INTERVAL = 60.0 / RATE_LIMIT_PER_MINUTE + 0.01


class _Throttle:
    """Минимальный интервал между запросами (на инстанс коннектора)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            pause = self._last + _MIN_INTERVAL - now
            if pause > 0:
                time.sleep(pause)
                now = time.monotonic()
            self._last = now


class WBSellerConnector(BaseConnector):
    """Wildberries Seller API (poll): карточки/остатки/заказы/финансы +
    опциональный push остатков. config: base_url, timeout_seconds,
    warehouses (JSON-строка WB warehouseID → наша локация), auto_create_orders
    (default off — §10.4), price_tolerance."""

    code = "wb_seller"
    display_name = "Wildberries Seller (маркетплейс)"
    capabilities = Capabilities(fetch=True, push=True)
    config_schema = {
        "base_url": {"type": "string",
                     "default": "https://seller-api.wildberries.ru"},
        "timeout_seconds": {"type": "int", "default": 60},
        # привязка WB warehouseID → наш location_id (JSON-строка; §10.1)
        "warehouses": {"type": "string", "default": ""},
        "auto_create_orders": {"type": "string", "default": "off"},
        "price_tolerance": {"type": "string", "default": "0"},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._throttle = _Throttle()

    def _headers(self) -> dict[str, str]:
        # WB: Authorization: <API-Token> БЕЗ Bearer (§3.1.1)
        return {
            "Authorization": str(self.credentials.get("api_token", "")),
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return (self.config.get("base_url")
                or "https://seller-api.wildberries.ru").rstrip("/") + path

    def _call(self, method: str, path: str, body: dict | None = None,
              params: dict | None = None) -> ConnectorResult:
        """Один HTTP-вызов WB: троттлинг 100/мин, egress-контроль,
        мягкие ошибки (auth_failed / rate_limited)."""
        url = self._url(path)
        try:
            self._throttle.wait()
            response = guarded_request(
                self.code, method, url, headers=self._headers(),
                json_body=body or {}, timeout=self.config.get("timeout_seconds", 60))
        except EgressBlocked as exc:
            log_egress(connector=self.code, url=url, status="blocked", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:  # сеть — не валит вызывающего (ретрай)
            log_egress(connector=self.code, url=url, status="error", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))
        log_egress(connector=self.code, url=url, status=response.status_code)
        if response.status_code in (401, 403):
            return ConnectorResult(ok=False, error=f"auth_failed: http {response.status_code}")
        if response.status_code == 429:
            return ConnectorResult(ok=False, error="rate_limited: http 429")
        if response.status_code >= 400:
            return ConnectorResult(ok=False, error=(
                f"http {response.status_code}: {response.text[:200]}"))
        try:
            return ConnectorResult(ok=True, data=response.json())
        except ValueError:
            return ConnectorResult(ok=False, error="invalid json response")

    # -- универсальный интерфейс SDK ----------------------------------

    def test_connection(self) -> ConnectorResult:
        """Дешёвая проверка: карточки с limit 1 (§7.1)."""
        result = self._call("POST", "/content/v2/get/cards/list",
                            {"settings": {"filter": {}, "cursor": "", "limit": 1}})
        if not result.ok:
            return result
        return ConnectorResult(ok=True)

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        """endpoint = kind: products|stocks|orders|transactions."""
        kind = endpoint or "products"
        if kind == "products":
            return self.fetch_products()
        if kind == "stocks":
            return self.fetch_stocks()
        if kind == "orders":
            return self.fetch_orders((params or {}).get("since"))
        if kind == "transactions":
            return self.fetch_transactions((params or {}).get("since"))
        return ConnectorResult(ok=False, error=f"unknown wb kind: {kind}")

    # -- виды синхронизации (§3.3) --------------------------------------

    def fetch_products(self) -> ConnectorResult:
        """/content/v2/get/cards/list → карточки (nmID, vendorCode, цена)."""
        result = self._call("POST", "/content/v2/get/cards/list",
                            {"settings": {"filter": {}, "cursor": "", "limit": 1000}})
        if not result.ok:
            return result
        cards = ((result.data or {}).get("cards")) or []
        out = []
        for card in cards:
            sizes = card.get("sizes") or [{}]
            price = ""
            for size in sizes:
                price = str((size.get("price") or {}).get("current") or "")
                break
            out.append({
                "nm_id": str(card.get("nmID", "")),
                "vendor_code": str(card.get("vendorCode", "")),
                "name": str(card.get("title", "") or card.get("name", "")),
                "price": price,
            })
        return ConnectorResult(ok=True, data=out)

    def fetch_stocks(self) -> ConnectorResult:
        """/api/v3/stocks → остатки по складам WB (снапшот)."""
        result = self._call("GET", "/api/v3/stocks",
                            params={"limit": 1000})
        if not result.ok:
            return result
        # WB отвечает списком строк с amountByWarehouse
        rows = (result.data or {}).get("stocks") or (result.data or [])
        out = []
        for row in rows if isinstance(rows, list) else []:
            nm_id = str(row.get("nmId", row.get("nmID", "")))
            for wh_id, amount in ((row.get("amountByWarehouse") or {})).items():
                out.append({
                    "nm_id": nm_id,
                    "warehouse_id": str(wh_id),
                    "qty": int(amount or 0),
                })
        return ConnectorResult(ok=True, data=out)

    def fetch_orders(self, since: str | None = None) -> ConnectorResult:
        """/api/v3/orders → заказы (UID, nmID, цена; инкремент по since)."""
        params: dict[str, Any] = {"limit": 1000}
        if since:
            params["dateFrom"] = str(since)
        result = self._call("GET", "/api/v3/orders", params=params)
        if not result.ok:
            return result
        rows = result.data if isinstance(result.data, list) else []
        out = []
        for po in rows:
            if not isinstance(po, dict):
                continue
            lines = [{
                "nm_id": str(po.get("nmId", "")),
                "vendor_code": str(po.get("article", "") or po.get("vendorCode", "")),
                "qty": int(po.get("quantity", 1) or 1),
                "price": str(po.get("totalPrice", po.get("price", "")) or ""),
            }]
            out.append({
                "uid": str(po.get("uid", po.get("orderUID", ""))),
                "status": str(po.get("isCancel", "")).lower() == "true" and "cancelled"
                          or ("delivered" if po.get("dateClosed") else "new"),
                "order_date": str(po.get("date", "") or po.get("createdAt", "")),
                "amount": str(po.get("totalPrice", po.get("price", "")) or ""),
                "currency": "RUB",
                "lines": lines,
            })
        return ConnectorResult(ok=True, data=out)

    def fetch_transactions(self, since: str | None = None) -> ConnectorResult:
        """/finance/v1/transactions → комиссии/логистика/хранение/штрафы/
        налог/выплаты (payment — записываем, деньги НЕ проводим §10.2)."""
        params: dict[str, Any] = {"limit": 1000}
        if since:
            params["dateFrom"] = str(since)
        result = self._call("GET", "/finance/v1/transactions", params=params)
        if not result.ok:
            return result
        ops = (result.data or {}).get("operations")
        if ops is None and isinstance(result.data, list):
            ops = result.data
        out = []
        for op in ops or []:
            if not isinstance(op, dict):
                continue
            out.append({
                "operation_id": str(op.get("operationId", op.get("id", ""))),
                "operation_type": str(op.get("operationType", "")),
                "amount": str(op.get("price" ) or op.get("amount", "")),
                "posted_at": str(op.get("date", "") or op.get("posted_at", "")),
                "items": op.get("items", []),
            })
        return ConnectorResult(ok=True, data=out)

    # -- push остатков (опционально, §3.3.5) --------------------------

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        """PUT /api/v3/stocks/{warehouseId}: [{nmId, vendorCode, stock}]."""
        body = payload or {}
        warehouse_id = str(body.get("warehouse_id", ""))
        stocks = body.get("stocks")
        if not warehouse_id or not stocks:
            return ConnectorResult(ok=False,
                                   error="wb push: need warehouse_id and stocks")
        result = self._call("PUT", f"/api/v3/stocks/{warehouse_id}",
                            {"stocks": stocks})
        if not result.ok:
            return result
        return ConnectorResult(ok=True, data=result.data)


registry.register(WBSellerConnector)
