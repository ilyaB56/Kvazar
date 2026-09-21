"""Эквайринг-коннекторы (sales-automation §5.3, ADR-001: сеть только здесь).

AcquiringConnector — каркас онлайн-оплаты: два механизма проверки
подлинности (HMAC-подпись verify_webhook для подписывающих провайдеров и
обязательный verify_by_fetch — повторный запрос статуса платежа через API,
ЮKassa нотификации не подписывает), normalize — единый словарь платежа.
YooKassaConnector — эталон; Т-Касса/Robokassa — каркасы до спроса пилотов.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from src.modules.integrations.connectors.egress import EgressBlocked, guarded_get
from src.modules.integrations.sdk import (
    BaseConnector,
    Capabilities,
    ConnectorResult,
    registry,
)


class AcquiringConnector(BaseConnector):
    """Каркас провайдера онлайн-оплаты.

    Общий контракт:
    - verify_webhook(headers, body) — проверка подписи (если провайдер
      подписывает; ЮKassa — нет, возвращает True только при наличии
      собственного механизма у наследника);
    - verify_by_fetch(payment_id) — авторизованный запрос статуса платежа:
      ОБЯЗАТЕЛЕН, если провайдер не подписывает нотификации (спека §3.4);
    - normalize(payload) — единый словарь: payment_id, status, amount,
      currency, buyer{email,phone,name}, lines[{external_id,sku,name,qty,
      price}], metadata;
    - external_key(payload) — ключ идемпотентности журнала вебхуков.

    Общие поля config: account_id (счёт зачисления — потребует оркестратор
    этапа B), default_digital_location.
    """

    capabilities = Capabilities(fetch=True, webhooks=True)
    config_schema = {
        "base_url": {"type": "string", "default": ""},
        "account_id": {"type": "string", "default": ""},
        "default_digital_location": {"type": "string", "default": ""},
    }

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:  # noqa: N802
        """Провайдер не подписывает нотификации → только verify_by_fetch."""
        return False

    def verify_by_fetch(self, payment_id: str) -> ConnectorResult:
        """Повторная проверка платежа через API провайдера (обязателен)."""
        raise NotImplementedError

    def normalize(self, payload: dict) -> dict:
        raise NotImplementedError

    def external_key(self, event_type: str, payload: dict) -> str:
        """Ключ идемпотентности: payment_id + event (у ЮKassa event_id нет)."""
        payment_id = self.payment_id(payload)
        return f"{payment_id}:{event_type}"

    def payment_id(self, payload: dict) -> str:
        raise NotImplementedError


class YooKassaConnector(AcquiringConnector):
    """Эталонный адаптер ЮKassa (Basic shopId:secretKey, API v3).

    Нотификации НЕ подписаны — истина в GET /v3/payments/{id}: сверяем
    статус succeeded и сумму с нотификацией. metadata платежа несёт
    external_item_id строк сайта (нормализация в lines).
    """

    code = "yookassa"
    display_name = "ЮKassa (онлайн-оплата)"
    config_schema = {
        **AcquiringConnector.config_schema,
        "base_url": {"type": "string", "default": "https://api.yookassa.ru/v3"},
    }

    def _headers(self) -> dict[str, str]:
        shop_id = str(self.credentials.get("shop_id", ""))
        secret = str(self.credentials.get("secret_key", ""))
        token = base64.b64encode(f"{shop_id}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def test_connection(self) -> ConnectorResult:
        # дешёвая авторизованная операция: список платежей (пустой — ок)
        base = self.config.get("base_url") or "https://api.yookassa.ru/v3"
        try:
            response = guarded_get(
                self.code, f"{base.rstrip('/')}/payments", params={"limit": 1},
                headers=self._headers(), timeout=self.config.get("timeout_seconds", 15),
            )
            if response.status_code == 200:
                return ConnectorResult(ok=True)
            return ConnectorResult(
                ok=False, error=f"yookassa http {response.status_code}: {response.text[:200]}")
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def fetch_payment(self, payment_id: str) -> ConnectorResult:
        """GET /v3/payments/{id} — источник истины о статусе и сумме."""
        base = self.config.get("base_url") or "https://api.yookassa.ru/v3"
        try:
            response = guarded_get(
                self.code, f"{base.rstrip('/')}/payments/{payment_id}",
                headers=self._headers(), timeout=self.config.get("timeout_seconds", 15),
            )
            if response.status_code == 200:
                return ConnectorResult(ok=True, data=response.json())
            return ConnectorResult(
                ok=False,
                error=f"yookassa http {response.status_code}: {response.text[:200]}",
            )
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def verify_by_fetch(self, payment_id: str) -> ConnectorResult:
        """Платёж существует и succeeded — иначе ок=False с причиной."""
        result = self.fetch_payment(payment_id)
        if not result.ok:
            return result
        payment = result.data
        status = payment.get("status")
        if status != "succeeded":
            return ConnectorResult(ok=False, data=payment, error=f"payment_status={status}")
        return result

    def payment_id(self, payload: dict) -> str:
        return str(payload.get("object", payload).get("id", ""))

    def normalize(self, payload: dict) -> dict:
        """Нотификация/ответ API → единый словарь платежа (§3.4)."""
        obj: dict[str, Any] = payload.get("object", payload)
        amount = obj.get("amount") or {}
        buyer: dict[str, Any] = {}
        buyer = obj.get("recipient") or {}
        if not isinstance(buyer, dict):
            buyer = {}
        receipt = obj.get("receipt_registration") or {}
        if isinstance(receipt, dict):
            buyer = receipt.get("buyer") or buyer
        metadata = obj.get("metadata") or {}
        lines = []
        raw_lines = metadata.get("lines")
        if isinstance(raw_lines, list):
            lines = [line for line in raw_lines if isinstance(line, dict)]
        elif metadata.get("item_sku") or metadata.get("sku"):
            # простой платёж одним товаром (реестр/демо): плоский
            # metadata {item_sku, qty[, price]} вместо массива lines
            sku = str(metadata.get("item_sku") or metadata.get("sku"))
            lines = [{
                "external_id": sku, "sku": sku,
                "qty": metadata.get("qty", 1),
                "price": str(metadata.get("price", "")
                             or amount.get("value", "")),
            }]
        return {
            "payment_id": str(obj.get("id", "")),
            "status": str(obj.get("status", "")),
            "amount": str(amount.get("value", "")),
            "currency": str(amount.get("currency", "RUB")),
            "buyer": {
                "email": buyer.get("email") or metadata.get("email") or "",
                "phone": buyer.get("phone") or metadata.get("phone") or "",
                "name": buyer.get("full_name") or buyer.get("name")
                           or metadata.get("name") or "",
            },
            "lines": [
                {
                    "external_id": str(line.get("external_id", line.get("id", ""))),
                    "sku": str(line.get("sku", "")),
                    "name": str(line.get("name", "")),
                    "qty": line.get("qty", 1),
                    "price": str(line.get("price", "")),
                }
                for line in lines
            ],
            "metadata": metadata,
        }


class TinkoffPaymentsConnector(AcquiringConnector):
    """Каркас Т-Кассы: класс и config зарегистрированы, адаптер — по спросу
    пилотов (спека §12.7)."""

    code = "tinkoff"
    display_name = "Т-Касса (онлайн-оплата) — каркас"

    def test_connection(self) -> ConnectorResult:
        return ConnectorResult(ok=False, error="tinkoff adapter not implemented yet")

    def payment_id(self, payload: dict) -> str:
        return str(payload.get("PaymentId", payload.get("paymentId", "")))

    def normalize(self, payload: dict) -> dict:
        raise NotImplementedError("tinkoff adapter not implemented yet")


class RobokassaConnector(AcquiringConnector):
    """Каркас Robokassa: HMAC-подпись (SignatureValue) — схема близка к
    HttpRestConnector.verify_webhook; адаптер — по спросу пилотов."""

    code = "robokassa"
    display_name = "Robokassa (онлайн-оплата) — каркас"

    def test_connection(self) -> ConnectorResult:
        return ConnectorResult(ok=False, error="robokassa adapter not implemented yet")

    def payment_id(self, payload: dict) -> str:
        return str(payload.get("InvId", payload.get("invoice_id", "")))

    def normalize(self, payload: dict) -> dict:
        raise NotImplementedError("robokassa adapter not implemented yet")


registry.register(YooKassaConnector)
registry.register(TinkoffPaymentsConnector)
registry.register(RobokassaConnector)
