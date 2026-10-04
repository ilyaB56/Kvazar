"""Внешний ИИ через интеграции (ADR-006, блок 1).

OpenAI-совместимый API (Z.ai, OpenAI и др.) и Anthropic — только chat;
эмбеддинги остаются на локальной модели (RAG-векторы должны быть одной
моделью для индексации и поиска). Bearer-токен — в credentials (Fernet),
модель — в config. Все вызовы — через guarded_request + журнал egress
(ADR-001: сеть только в connectors/).
"""

from __future__ import annotations

import logging

import httpx

from src.modules.integrations.connectors.egress import (
    EgressBlocked, guarded_request, log_egress,
)
from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry

logger = logging.getLogger(__name__)


class ExternalAIConnector(BaseConnector):
    """Внешний LLM для чата: config {base_url, model, style, timeout}.

    style=openai — POST {base}/chat/completions, Authorization: Bearer;
    style=anthropic — POST {base}/v1/messages, x-api-key +
    anthropic-version (system-сообщение выносится на верхний уровень).
    """

    code = "external_ai"
    display_name = "Внешний ИИ (Z.ai / OpenAI / Anthropic)"
    capabilities = Capabilities(fetch=True, push=True)
    config_schema = {
        # z.ai: OpenAI-совместимый путь — /api/paas/v4 (дефолт /v1 давал
        # 404; живой зонд 2026-10-04: /api/paas/v4 → 401, /v1 → 404)
        "base_url": {"type": "string", "required": True,
                     "default": "https://api.z.ai/api/paas/v4"},
        # dropdown в UI (ConnectionsView рендерит enum как Select);
        # коннектор принимает любую строку — список лишь подсказка.
        # glm-4-flash/glm-4-plus на межд. API z.ai не существуют (ошибка
        # 1211 Unknown Model — проверено живым запросом 2026-10-04)
        "model": {"type": "enum",
                  "values": ["glm-4.6", "glm-4.5", "glm-4.5-air",
                             "gpt-4o-mini", "claude-3-haiku"],
                  "required": True, "default": "glm-4.6"},
        "style": {"type": "enum", "values": ["openai", "anthropic"],
                  "default": "openai"},
        "timeout_seconds": {"type": "int", "default": 120},
    }

    def _headers(self) -> dict[str, str]:
        key = str(self.credentials.get("api_key", ""))
        if self.config.get("style") == "anthropic":
            return {"x-api-key": key, "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {key}",
                "Content-Type": "application/json"}

    def test_connection(self) -> ConnectorResult:
        """Дешёвая проверка: пустой chat-запрос (max_tokens=1)."""
        try:
            result = self.push(payload={"messages": [
                {"role": "user", "content": "ping"}], "_probe": True})
            if result.ok:
                return ConnectorResult(ok=True)
            return ConnectorResult(ok=False, error=result.error)
        except EgressBlocked as exc:
            return ConnectorResult(ok=False, error=str(exc))

    # -- chat ----------------------------------------------------------

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        body = payload or {}
        messages = body.get("messages", [])
        model = str(self.config.get("model", ""))
        if not model:
            return ConnectorResult(ok=False, error="external_ai: config.model is empty")
        base = self.config.get("base_url", "").rstrip("/")
        anthropic = self.config.get("style") == "anthropic"
        url = base + (endpoint or ("/v1/messages" if anthropic else "/chat/completions"))
        probe = bool(body.get("_probe"))
        try:
            if anthropic:
                system = "\n".join(m["content"] for m in messages if m["role"] == "system")
                rest = [m for m in messages if m["role"] != "system"]
                request = {"model": model, "max_tokens": 1 if probe else 4096,
                           "messages": [
                               {"role": m["role"], "content": m["content"]}
                               for m in rest]}
                if system:
                    request["system"] = system
            else:
                request = {"model": model,
                           "max_tokens": 1 if probe else 4096,
                           "messages": messages}
            response = guarded_request(
                self.code, "POST", url, headers=self._headers(),
                json_body=request,
                timeout=self.config.get("timeout_seconds", 120))
        except EgressBlocked as exc:
            log_egress(connector=self.code, url=url, status="blocked", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:  # сеть недоступна — не валит вызывающего
            log_egress(connector=self.code, url=url, status="error", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))
        content = _extract_content(response.json() if response.content else {},
                                   anthropic)
        ok = response.status_code < 400 and content is not None
        log_egress(connector=self.code, url=url, status=response.status_code,
                   error="" if ok else f"no content: {response.text[:120]}")
        if not ok:
            detail = response.text[:200] if content is None else ""
            return ConnectorResult(
                ok=False, error=f"http {response.status_code}: {detail}"[:300])
        return ConnectorResult(ok=True, data={"content": content})

    # -- embeddings: НЕ на внешнем ИИ -----------------------------------

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        return ConnectorResult(
            ok=False,
            error="external_ai: embeddings остаются на локальной модели "
                  "(RAG-векторы одной модели)")


def _extract_content(data: dict, anthropic: bool) -> str | None:
    """Ответ провайдера → текст (None = провал/пусто)."""
    try:
        if anthropic:
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = data["choices"][0]["message"]["content"] or ""
        return text if text.strip() else None
    except (KeyError, IndexError, TypeError):
        return None


registry.register(ExternalAIConnector)
