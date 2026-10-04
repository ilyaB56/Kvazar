"""Публичные контракты ядра.

Модули зависят только от этого файла. Ядро не импортирует модули напрямую —
они регистрируются через Manifest (см. core.plugins).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True)
class Manifest:
    """Декларация модуля: имя, версия, схема БД, зависимости, роутеры."""

    name: str
    version: str
    db_schema: str
    depends_on: tuple[str, ...] = ()
    routers: tuple["APIRouter", ...] = ()
    event_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    connector_types: tuple[type, ...] = ()  # классы BaseConnector модуля
    # URL-префикс модуля (/api/v1/<url_prefix>); пусто — использовать name.
    # Например, mgmt_accounting живёт под /api/v1/accounting.
    url_prefix: str = ""
    # Реестр доменных адаптеров ракурсов ведения (devtools §7.2):
    # {"counterparty": {"list": fn, "create": fn, "update": fn}} —
    # модуль регистриует сервисные функции; ядро зовёт их in-process
    # (не HTTP — ADR-001; не импорт модуля из ядра — инверсия зависимостей)
    devtools_adapters: dict[str, dict[str, Callable[..., Any]]] = field(default_factory=dict)


# Контекст, который ядро передаёт модулю при инициализации.
# Расширять по мере роста (например, доступ к настройкам, клиент Redis).
@dataclass
class ModuleContext:
    settings: Any
