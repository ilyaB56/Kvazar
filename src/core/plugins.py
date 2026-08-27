"""Загрузчик модулей-плагинов.

Ядро не импортирует модули напрямую: список модулей задаётся здесь в MANIFESTS,
роутеры и обработчики событий подключаются к приложению на старте.
Новый модуль добавляется одной строкой + его миграцией в Alembic.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.core import events
from src.core.contracts import Manifest

logger = logging.getLogger(__name__)


def _core_manifest() -> Manifest:
    from src.core import router as core_router

    return Manifest(
        name="core",
        version="0.1.0",
        db_schema="erp_core",
        routers=(core_router.router,),
    )


def _integrations_manifest() -> Manifest:
    from src.modules.integrations.manifest import manifest

    return manifest


def _mgmt_accounting_manifest() -> Manifest:
    from src.modules.mgmt_accounting.manifest import manifest

    return manifest


# Порядок = порядок зависимостей. Ядро всегда первым.
MANIFESTS: list[Manifest] = [
    _core_manifest(),
    _integrations_manifest(),
    _mgmt_accounting_manifest(),
]


def register_event_handlers() -> list[Manifest]:
    """Подписать обработчики событий всех модулей к шине.

    Вызывается и приложением (main), и Celery-воркером: диспетчер outbox
    живёт в воркере, подписчики должны быть зарегистрированы в обоих
    процессах — иначе события помечаются processed без доставки.
    """
    for m in MANIFESTS:
        for event_name, handler in m.event_handlers.items():
            events.subscribe(event_name, handler)
    # Telegram-уведомления по белому списку событий (showcase-chain, этап D)
    from src.modules.integrations.notify import register_notification_handlers

    register_notification_handlers()
    # Мини-исполнитель рецептов: trigger_event -> api_call (этап F)
    from src.modules.integrations.recipes_executor import register_recipe_handlers

    register_recipe_handlers()
    return MANIFESTS


def install_modules(app: FastAPI) -> list[Manifest]:
    """Подключить роутеры и подписки всех активных модулей к приложению."""
    seen: set[str] = set()
    for m in MANIFESTS:
        for dep in m.depends_on:
            if dep not in seen:
                msg = f"module {m.name}: dependency {dep} not loaded before it"
                raise RuntimeError(msg)
        for router in m.routers:
            prefix = f"/api/v1/{m.url_prefix or m.name}" if m.name != "core" else "/api/v1"
            app.include_router(router, prefix=prefix)
        register_event_handlers()
        seen.add(m.name)
        logger.info("module installed: %s %s", m.name, m.version)
    return MANIFESTS
