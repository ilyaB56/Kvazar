"""Единая точка вызова LLM для модуля ai_agent (через коннекторы, ADR-001).

Провайдер выбирается настройкой AI_PROVIDER: connection «ollama» для живой
модели, «llm-mock» для тестов. Модуль сам httpx не импортирует.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.config import get_settings
from src.db import SessionLocal
from src.modules.integrations import models as im
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict

logger = logging.getLogger(__name__)


def _connection_for(db, code: str) -> im.Connection | None:
    return db.scalar(select(im.Connection).where(
        im.Connection.connector_code == code,
        im.Connection.is_active.is_(True),
    ).order_by(im.Connection.created_at.desc()).limit(1))


def _build(code: str):
    """Собрать коннектор по активному connection или пустому конфигу (мок)."""
    db = SessionLocal()
    try:
        connection = _connection_for(db, code)
        if connection is not None:
            return connector_registry.build(
                code, connection.config, decrypt_dict(connection.credentials_enc))
    finally:
        db.close()
    return connector_registry.build(code, {}, {})


def chat(messages: list[dict], scenario: str = "default",
         scripted_content: str | None = None) -> dict[str, Any]:
    """Сообщения → {content}. scenario/scripted_content — управление моком."""
    settings = get_settings()
    connector = _build(settings.ai_provider)
    payload: dict[str, Any] = {"messages": messages}
    if settings.ai_provider == "llm_mock":
        payload["scenario"] = scenario
        if scripted_content is not None:
            payload["scripted_content"] = scripted_content
    result = connector.push(payload=payload)
    if not result.ok:
        raise RuntimeError(f"LLM chat failed: {result.error}")
    return result.data or {"content": ""}


def embed(text: str) -> list[float]:
    """Текст → вектор (AI_EMBED_MODEL)."""
    connector = _build(get_settings().ai_provider)
    result = connector.fetch(params={"text": text})
    if not result.ok:
        raise RuntimeError(f"LLM embed failed: {result.error}")
    vector = (result.data or {}).get("embedding", [])
    if not vector:
        raise RuntimeError("LLM embed returned empty vector")
    return vector
