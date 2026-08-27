"""Мини-исполнитель рецептов (showcase-chain, этап F, опциональный).

definition = {trigger_event, action: {type: 'api_call', connection_id,
method, endpoint, body_template}}. Обработчик триггера рендерит body из
payload и зовёт внутренний connection (http_rest) с X-API-Token из
credentials.api_key (config: auth_style=header, auth_header_name=X-API-Token).
Сеть — только через коннектор (ADR-001).

Ограничение мини-версии: подписки на trigger_event ставятся при старте
процессов (api + worker) по опубликованным рецептам; новый рецепт
подхватывается после рестарта. Итог — аудит recipe.executed/recipe.failed.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select

from src.core.models import AuditEvent
from src.db import SessionLocal
from src.modules.integrations import models as m
from src.modules.integrations.connectors.builtin import registry as connector_registry
from src.modules.integrations.crypto import decrypt_dict

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render_body(body_template: str, payload: dict) -> dict:
    """Шаблон -> JSON-тело. Плейсхолдеры {ключ} заменяются regex-ом: JSON-скобки
    шаблона не конфликтуют с подстановкой (в отличие от str.format).
    Не-JSON результат оборачивается в {"text": ...}."""
    def substitute(match: re.Match) -> str:
        return str(payload.get(match.group(1), match.group(0)))

    rendered = _PLACEHOLDER.sub(substitute, body_template or "")
    try:
        parsed = json.loads(rendered)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except ValueError:
        return {"text": rendered}


def execute_recipe_action(recipe: m.Recipe, payload: dict) -> bool:
    """Одно действие api_call через connection. Возвращает ok."""
    db = SessionLocal()
    try:
        action = (recipe.definition or {}).get("action", {})
        connection = db.get(m.Connection, action.get("connection_id"))
        if connection is None:
            logger.warning("recipe %s: connection not found", recipe.name)
            return False
        connector = connector_registry.build(
            connection.connector_code, connection.config,
            decrypt_dict(connection.credentials_enc),
        )
        endpoint = action.get("endpoint", "")
        method = str(action.get("method", "POST")).upper()
        body = render_body(action.get("body_template", ""), payload)
        if method == "GET":
            result = connector.fetch(endpoint)
        else:
            result = connector.push(endpoint, body)
        db.add(AuditEvent(
            action="recipe.executed" if result.ok else "recipe.failed",
            entity_type="recipe", entity_id=str(recipe.id),
            payload={"name": recipe.name, "method": method,
                     "endpoint": endpoint, "error": result.error},
        ))
        db.commit()
        return result.ok
    finally:
        db.close()


def register_recipe_handlers() -> None:
    from src.core import events

    db = SessionLocal()
    try:
        recipes = db.scalars(select(m.Recipe).where(m.Recipe.is_published.is_(True))).all()
        triggers = {r.definition.get("trigger_event")
                    for r in recipes if (r.definition or {}).get("trigger_event")}
    finally:
        db.close()

    for trigger in triggers:
        def handler(payload: dict, _trigger=trigger) -> None:
            # исполнение уходит в воркер (блокирующий api_call)
            from src.modules.integrations.tasks import recipe_task

            session = SessionLocal()
            try:
                matching = session.scalars(select(m.Recipe).where(
                    m.Recipe.is_published.is_(True))).all()
            finally:
                session.close()
            for recipe in matching:
                if (recipe.definition or {}).get("trigger_event") != _trigger:
                    continue
                try:
                    recipe_task.delay(str(recipe.id), dict(payload))
                except Exception:  # noqa: BLE001 — рецепт не валит диспетчер
                    logger.exception("recipe %s: queueing failed", recipe.name)

        events.subscribe(trigger, handler)
    if triggers:
        logger.info("recipe triggers registered: %s", sorted(triggers))
