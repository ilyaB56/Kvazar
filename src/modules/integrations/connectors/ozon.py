"""Коннектор Ozon Seller API (ozon-connector-spec, этап A).

Товары/цены/остатки/заказы/транзакции — poll (вебхуков в v1 нет).
Сеть — только здесь (ADR-001): guarded_request + allowlist
api-seller.ozon.ru. Аутентификация: заголовки Client-Id + Api-Key
(Fernet, credentials), НЕ Bearer. Моки — config.base_url.

Остатки Ozon — чужой склад: снапшот, НЕ создают movements в ERP (§3.1).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.modules.integrations.connectors.egress import (
    EgressBlocked, guarded_request, log_egress,
)
from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry

logger = logging.getLogger(__name__)


class OzonSellerConnector(BaseConnector):
    """Ozon Seller API v3–v5 (poll): catalog/stocks/orders/transactions
    + опциональный push остатков. config: base_url, timeout_seconds,
    warehouse_ids, auto_create_orders, price_tolerance (§3.2)."""

    code = "ozon_seller"
    display_name = "Ozon Seller (маркетплейс)"
    capabilities = Capabilities(fetch=True, push=True)
    config_schema = {
        "base_url": {"type": "string", "default": "https://api-seller.ozon.ru"},
        "timeout_seconds": {"type": "int", "default": 60},
        # push остатков: список складов Ozon (строка через запятую в config)
        "warehouse_ids": {"type": "string", "default": ""},
        # автосоздание sales_orders (default off — решение ревью §10.4)
        "auto_create_orders": {"type": "string", "default": "off"},
        "price_tolerance": {"type": "string", "default": "0"},
    }

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": str(self.credentials.get("client_id", "")),
            "Api-Key": str(self.credentials.get("api_key", "")),
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return (self.config.get("base_url")
                or "https://api-seller.ozon.ru").rstrip("/") + path

    def _call(self, method: str, path: str, body: dict | None = None) -> ConnectorResult:
        """Один HTTP-вызов Ozon с журналом egress и мягкими ошибками."""
        url = self._url(path)
        try:
            response = guarded_request(
                self.code, method, url, headers=self._headers(),
                json_body=body or {},
                timeout=self.config.get("timeout_seconds", 60))
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
        """Дешёвая проверка: остатки с limit 1 (§7.1)."""
        result = self._call("POST", "/v4/product/info/stocks",
                            {"filter": {}, "limit": 1})
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
        return ConnectorResult(ok=False, error=f"unknown ozon kind: {kind}")

    # -- виды синхронизации (§3.3) --------------------------------------

    def fetch_products(self) -> ConnectorResult:
        """/v3/product/list → список товаров (offer_id, id, name);
        цены — /v5/product/info/prices (v4 устарел)."""
        result = self._call("POST", "/v3/product/list",
                            {"filter": {}, "limit": 1000})
        if not result.ok:
            return result
        items = (result.data or {}).get("result", {}).get("items", [])
        products = [{
            "offer_id": str(p.get("offer_id", "")),
            "product_id": str(p.get("product_id", "")),
            "name": str(p.get("name", "")),
        } for p in items if p.get("offer_id")]
        prices = self._call("POST", "/v5/product/info/prices",
                            {"filter": {}, "limit": 1000})
        price_map: dict[str, str] = {}
        if prices.ok:
            for row in ((prices.data or {}).get("result") or {}).get("items", []):
                price = (row.get("price") or {}).get("price") or ""
                if row.get("offer_id") and price != "":
                    price_map[str(row["offer_id"])] = str(price)
        out = [{**p, "price": price_map.get(p["offer_id"])} for p in products]
        return ConnectorResult(ok=True, data=out)

    def fetch_stocks(self) -> ConnectorResult:
        """/v4/product/info/stocks → снапшот остатков по складам Ozon."""
        result = self._call("POST", "/v4/product/info/stocks",
                            {"filter": {}, "limit": 1000})
        if not result.ok:
            return result
        items = (result.data or {}).get("result", {}).get("items", [])
        out = []
        for row in items:
            for wh in row.get("stocks", []):
                out.append({
                    "offer_id": str(row.get("offer_id", "")),
                    "warehouse_id": str(wh.get("warehouse_id", "")),
                    "qty": int(wh.get("present", 0) or 0),
                })
        return ConnectorResult(ok=True, data=out)

    def fetch_orders(self, since: str | None = None) -> ConnectorResult:
        """/v5/order/list (инкремент по since; delivered/cancelled)."""
        body: dict[str, Any] = {"filter": {"status": ""}, "limit": 1000}
        if since:
            body["filter"]["since"] = str(since)
        result = self._call("POST", "/v5/order/list", body)
        if not result.ok:
            return result
        # мок/прод: заказы приходят объектами posting — нормализуем
        raw = (result.data or {}).get("result", {})
        postings = raw.get("postings") if isinstance(raw, dict) else raw
        out = []
        for po in postings or []:
            if not isinstance(po, dict):
                continue
            lines = []
            for p in po.get("products", []):
                lines.append({
                    "offer_id": str(p.get("offer_id", "")),
                    "qty": int(p.get("quantity", 1) or 1),
                    "price": str(p.get("price", "")),
                })
            out.append({
                "posting_number": str(po.get("posting_number", "")),
                "status": str(po.get("status", "new")),
                "order_date": str(po.get("order_date" ) or po.get("created_at", "")),
                "amount": str(po.get("price", "") or po.get("amount", "")),
                "currency": str(po.get("currency", "RUB")),
                "lines": lines,
            })
        return ConnectorResult(ok=True, data=out)

    def fetch_transactions(self, since: str | None = None) -> ConnectorResult:
        """/v1/report/transactions → комиссии/логистика/реклама/возвраты."""
        body: dict[str, Any] = {"filter": {}, "limit": 1000}
        if since:
            body["filter"]["since"] = str(since)
        result = self._call("POST", "/v1/report/transactions", body)
        if not result.ok:
            return result
        raw = (result.data or {}).get("result", {})
        ops = raw.get("operations") if isinstance(raw, dict) else raw
        out = []
        for op in ops or []:
            if not isinstance(op, dict):
                continue
            out.append({
                "operation_id": str(op.get("operation_id", "")),
                "operation_type": str(op.get("operation_type_name",
                                             op.get("operation_type", ""))),
                "amount": str(op.get("amount", "")),
                "posted_at": str(op.get("date" ) or op.get("posted_at", "")),
                "items": op.get("items", []),
            })
        return ConnectorResult(ok=True, data=out)

    # -- push остатков (опционально, §3.3 п.5) --------------------------

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        """POST /v1/product/import/stocks: [{offer_id, product_id, stock}]."""
        stocks = (payload or {}).get("stocks")
        if not stocks:
            return ConnectorResult(ok=False, error="ozon push: no stocks payload")
        result = self._call("POST", "/v1/product/import/stocks",
                            {"stocks": stocks})
        if not result.ok:
            return result
        return ConnectorResult(ok=True, data=result.data)


registry.register(OzonSellerConnector)
