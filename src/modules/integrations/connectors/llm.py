"""LLM-коннекторы (ai-agent, этап A): Ollama (локально, ADR-006), мок для
тестов и заготовка OpenAI-совместимого внешнего провайдера (выключена
флагом ENABLE_EXTERNAL_LLM, default false).

Все сетевые вызовы LLM — здесь и только здесь (ADR-001).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx

from src.config import get_settings
from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry

EMBED_DIM = 1024  # bge-m3


def _hash_embed(text: str) -> list[float]:
    """Псевдовектор для мока: bag-of-words по токенам, стабильный (hash-based).

    Даёт осмысленный косинус для совпадающих слов — детерминированные
    сценарии поиска без реальной модели.
    """
    vector = [0.0] * EMBED_DIM
    for token in re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower()):
        digest = hashlib.md5(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % EMBED_DIM
        vector[index] += 1.0
    norm = sum(v * v for v in vector) ** 0.5
    if norm:
        vector = [round(v / norm, 6) for v in vector]
    return vector


class OllamaConnector(BaseConnector):
    """Локальный Ollama: /api/chat и /api/embed. Длинные таймауты."""

    code = "ollama"
    display_name = "Ollama (локальный LLM)"
    capabilities = Capabilities(fetch=True, push=True)
    config_schema = {
        "base_url": {"type": "string", "default": "http://ollama:11434"},
        "timeout_seconds": {"type": "int", "default": 300},
    }

    def _url(self, path: str) -> str:
        return self.config.get("base_url", "http://ollama:11434").rstrip("/") + path

    def test_connection(self) -> ConnectorResult:
        try:
            response = httpx.get(self._url("/api/tags"), timeout=15)
            return ConnectorResult(ok=response.status_code == 200,
                                   error="" if response.status_code == 200 else response.text[:200])
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def pull_model(self, model: str, base_url: str | None = None) -> ConnectorResult:
        """Скачать/запечь модель (прогрев после первого старта профиля ai)."""
        url = (base_url or self.config.get("base_url", "http://ollama:11434")).rstrip("/")
        try:
            response = httpx.post(f"{url}/api/pull", json={"name": model}, timeout=1800)
            return ConnectorResult(ok=response.status_code == 200,
                                   error="" if response.status_code == 200 else response.text[:300])
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        """Чат: payload = {messages: [{role, content}], model?} → content."""
        body = payload or {}
        try:
            response = httpx.post(self._url(endpoint or "/api/chat"), json={
                "model": body.get("model") or get_settings().ai_chat_model,
                "messages": body.get("messages", []),
                "stream": False,
            }, timeout=self.config.get("timeout_seconds", 300))
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            return ConnectorResult(ok=True, data={"content": content})
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        """Эмбеддинги: params = {text} → {embedding: [...]}; для RAG."""
        try:
            response = httpx.post(self._url(endpoint or "/api/embed"), json={
                "model": (params or {}).get("model") or get_settings().ai_embed_model,
                "input": (params or {}).get("text", ""),
            }, timeout=self.config.get("timeout_seconds", 300))
            response.raise_for_status()
            embeddings = response.json().get("embeddings", [[]])
            return ConnectorResult(ok=True, data={"embedding": embeddings[0] if embeddings else []})
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))


class LlmMockConnector(BaseConnector):
    """Детерминированный мок: сценарий задаёт вызов (payload['scenario']),
    embeddings — стабильные hash-based псевдовекторы."""

    code = "llm_mock"
    display_name = "LLM-мок (тесты)"
    capabilities = Capabilities(fetch=True, push=True)
    config_schema: dict[str, Any] = {}

    def test_connection(self) -> ConnectorResult:
        return ConnectorResult(ok=True)

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        body = payload or {}
        scenario = body.get("scenario", "default")
        # сценарии описывает сам вызов; в ответе — content по сценарию
        content = body.get("scripted_content")
        if content is None:
            content = json.dumps({"scenario": scenario}, ensure_ascii=False)
        return ConnectorResult(ok=True, data={"content": content,
                                              "scenario": scenario})

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        return ConnectorResult(ok=True, data={"embedding": _hash_embed((params or {}).get("text", ""))})


registry.register(OllamaConnector)
registry.register(LlmMockConnector)
