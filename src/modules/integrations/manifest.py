"""Манифест модуля integrations — точка регистрации в ядре."""

from __future__ import annotations

import logging

from src.core.contracts import Manifest
from src.modules.integrations.router import router

logger = logging.getLogger(__name__)


def _on_data_fetched(payload: dict) -> None:
    """Пример обработчика события внутри модуля. Сюда же подключится ИИ-агент."""
    logger.info("integration data fetched: %s items", len(payload.get("items", [])))


def _send_reset_email(payload: dict) -> None:
    """Точка входа обработчика (импорт внутри — циклическая зависимость
    manifest ↔ password_recovery через models)."""
    from src.modules.integrations.password_recovery import send_reset_email

    send_reset_email(payload)


manifest = Manifest(
    name="integrations",
    version="0.1.0",
    db_schema="integrations",
    depends_on=("core",),
    routers=(router,),
    event_handlers={
        "integration.data.fetched": _on_data_fetched,
        # письмо восстановления пароля — платформенным SMTP (этап D, §7.5)
        "core.password.reset_requested": _send_reset_email,
    },
)
