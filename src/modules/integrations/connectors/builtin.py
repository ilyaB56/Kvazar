"""Встроенные коннекторы-референсы.

HttpRestConnector — универсальный: подключает ЛЮБОЙ сервис с REST API
(банки, CRM, госсервисы) без написания кода. Этого достаточно для
большинства интеграций «из коробки».
"""

from __future__ import annotations

import hmac
import hashlib

import httpx

from src.modules.integrations.connectors.egress import (
    EgressBlocked, guarded_get, guarded_post,
)
from src.modules.integrations.sdk import (
    BaseConnector,
    Capabilities,
    ConnectorResult,
    registry,
)


class HttpRestConnector(BaseConnector):
    code = "http_rest"
    display_name = "Универсальный REST/HTTP"
    capabilities = Capabilities(fetch=True, push=True, webhooks=True)
    config_schema = {
        "base_url": {"type": "string", "required": True},
        "auth_style": {"type": "enum", "values": ["bearer", "header", "basic", "none"], "default": "bearer"},
        "auth_header_name": {"type": "string", "default": "Authorization"},
        "timeout_seconds": {"type": "int", "default": 30},
        "health_path": {"type": "string", "default": ""},
    }

    def _headers(self) -> dict[str, str]:
        # Без заданного секрета auth-заголовок не ставится вовсе:
        # «Bearer » с пустым токеном невалиден и ломает даже публичный /health.
        style = self.config.get("auth_style", "bearer")
        name = self.config.get("auth_header_name", "Authorization")
        api_key = self.credentials.get("api_key", "")
        headers: dict[str, str] = {}
        if style == "basic":
            if self.credentials.get("basic_token"):
                headers = {name: f"Basic {self.credentials['basic_token']}"}
        elif style not in ("none",) and api_key:
            headers = {name: api_key} if style == "header" else {name: f"Bearer {api_key}"}
        return headers

    def _url(self, endpoint: str) -> str:
        return self.config.get("base_url", "").rstrip("/") + "/" + endpoint.lstrip("/")

    def _timeout(self) -> int:
        return int(self.config.get("timeout_seconds", 30))

    def test_connection(self) -> ConnectorResult:
        # egress-контроль обязателен (P1 п.7): strict + незнакомый домен →
        # EgressBlocked «домен X не в белом списке…»
        try:
            response = guarded_get(
                self.code, self._url(self.config.get("health_path", "")),
                headers=self._headers(), timeout=self._timeout())
            ok = response.status_code < 500
            return ConnectorResult(ok=ok, data={"status": response.status_code})
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        try:
            response = guarded_get(
                self.code, self._url(endpoint), headers=self._headers(),
                params=params or None, timeout=self._timeout())
            response.raise_for_status()
            return ConnectorResult(ok=True, data=response.json())
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        try:
            response = guarded_post(
                self.code, self._url(endpoint), headers=self._headers(),
                json=payload or {}, timeout=self._timeout())
            response.raise_for_status()
            data = response.json() if response.content else None
            return ConnectorResult(ok=True, data=data)
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        # HMAC-подпись в X-Signature (схема как у большинства платёжных систем)
        secret = self.credentials.get("webhook_secret", "").encode()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        provided = headers.get("x-signature", headers.get("X-Signature", ""))
        return hmac.compare_digest(expected, provided)


class BankApiConnector(HttpRestConnector):
    """Шаблон для банковских интерфейсов (НСПК/открытые API банков).

    Наследует универсальную HTTP-логику, добавляет типовые особенности:
    строгий HMAC и лимиты запросов. Дополняйте по документации конкретного банка.
    """

    code = "bank_api"
    display_name = "Банковский API (шаблон)"
    capabilities = Capabilities(fetch=True, push=True, webhooks=True)
    config_schema = {
        "base_url": {"type": "string", "required": True},
        "bank_code": {"type": "string", "required": True},
        "timeout_seconds": {"type": "int", "default": 30},
    }

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self.credentials.get("webhook_secret", "").encode()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        provided = headers.get("x-bank-signature", "")
        return hmac.compare_digest(expected, provided)


registry.register(HttpRestConnector)
registry.register(BankApiConnector)
