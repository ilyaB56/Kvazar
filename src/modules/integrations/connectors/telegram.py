"""Коннектор Telegram Bot API (showcase-chain, этап D). Push-only.

push → POST {api_base}/bot{token}/sendMessage с {chat_id, text}.
api_base из config (дефолт https://api.telegram.org; в тестах/приёмке
подменяется мок-сервером). Сетевой код — только внутри коннектора (ADR-001).
"""

from __future__ import annotations

import httpx

from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry


class TelegramBotConnector(BaseConnector):
    code = "telegram_bot"
    display_name = "Telegram (уведомления)"
    capabilities = Capabilities(push=True)
    config_schema = {
        "api_base": {"type": "string", "default": "https://api.telegram.org"},
        "timeout_seconds": {"type": "int", "default": 15},
    }

    def _url(self, method: str) -> str:
        base = self.config.get("api_base", "https://api.telegram.org").rstrip("/")
        token = self.credentials.get("bot_token", "")
        return f"{base}/bot{token}/{method}"

    def test_connection(self) -> ConnectorResult:
        try:
            response = httpx.get(self._url("getMe"), timeout=self.config.get("timeout_seconds", 15))
            return ConnectorResult(ok=response.status_code == 200,
                                   error="" if response.status_code == 200 else response.text[:200])
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        body = {"chat_id": (payload or {}).get("chat_id"), "text": (payload or {}).get("text", "")}
        if body["chat_id"] is None:
            return ConnectorResult(ok=False, error="chat_id is required")
        try:
            response = httpx.post(self._url("sendMessage"), json=body,
                                  timeout=self.config.get("timeout_seconds", 15))
            ok = response.status_code == 200
            return ConnectorResult(ok=ok, error="" if ok else response.text[:200])
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))


registry.register(TelegramBotConnector)
