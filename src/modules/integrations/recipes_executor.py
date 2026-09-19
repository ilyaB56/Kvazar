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
import uuid
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
    """Действие рецепта: sales_flow или api_call. Возвращает ok."""
    db = SessionLocal()
    try:
        action = (recipe.definition or {}).get("action", {})
        action_type = action.get("type", "api_call")

        if action_type == "sales_flow":
            # оркестратор «платёж → документы → коды» (sales-automation §3.2)
            from src.modules.integrations import sales_flow as sf
            from src.modules.integrations.sales_flow import AccountingApi
            payment_id = payload.get("payment_id")
            if not payment_id:
                logger.warning("recipe %s: sales_flow без payment_id", recipe.name)
                return False
            payment = db.get(m.OnlinePayment, uuid.UUID(str(payment_id)))
            if payment is None:
                logger.warning("recipe %s: payment %s not found", recipe.name, payment_id)
                return False
            # AccountingApi из http_rest-подключения (api_connection_id в definition)
            api_conn_id = (recipe.definition or {}).get("api_connection_id")
            api_conn = db.get(m.Connection, api_conn_id) if api_conn_id else None
            if api_conn is None:
                logger.warning("recipe %s: api_connection_id not found", recipe.name)
                return False
            api_creds = decrypt_dict(api_conn.credentials_enc) if api_conn.credentials_enc else {}
            api = AccountingApi(
                # worker живёт в compose-сети: API — по имени сервиса
                base_url=api_conn.config.get("base_url", "http://api:8000/api/v1"),
                api_token=api_creds.get("api_key", ""),
            )
            try:
                sf.run_sales_flow(db, payment=payment, recipe=recipe, api=api)
                return True
            except Exception:
                logger.exception("recipe %s: sales_flow failed for payment %s",
                                 recipe.name, payment_id)
                return False

        # --- api_call (прежний путь) ---
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
            # исполнение уходит в воркер (блокирующий api_call) — КРОМЕ
            # платежей: флоу payment.received вебхук исполняет синхронно
            # (этап B, ответ 202 несёт статус флоу); воркерный дубль не
            # нужен и опасен — side-effects (заказ/отгрузка) живут в
            # отдельных транзакциях API, повтор гонит дубли документов
            from src.modules.integrations.tasks import recipe_task

            if _trigger == "integration.payment.received":
                return

            session = SessionLocal()
            try:
                matching = session.scalars(select(m.Recipe).where(
                    m.Recipe.is_published.is_(True))).all()
                # §3.2: рецепт привязан к провайдеру платежей
                # (action.connection_id) — чужие подключения не обрабатываем;
                # иначе N тестовых/демо-рецептов разводят N флоу на платёж
                pay_conn = None
                pay_id = payload.get("payment_id")
                if pay_id:
                    pay_conn = session.scalar(select(m.OnlinePayment.connection_id).where(
                        m.OnlinePayment.id == uuid.UUID(str(pay_id))))
            finally:
                session.close()
            for recipe in matching:
                definition = recipe.definition or {}
                if definition.get("trigger_event") != _trigger:
                    continue
                action_conn = (definition.get("action") or {}).get("connection_id")
                if action_conn and pay_conn is not None                         and str(action_conn) != str(pay_conn):
                    continue
                try:
                    recipe_task.delay(str(recipe.id), dict(payload))
                except Exception:  # noqa: BLE001 — рецепт не валит диспетчер
                    logger.exception("recipe %s: queueing failed", recipe.name)

        events.subscribe(trigger, handler)
    if triggers:
        logger.info("recipe triggers registered: %s", sorted(triggers))
