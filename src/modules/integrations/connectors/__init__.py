"""Импорт модулей коннекторов регистрирует их в общем registry (sdk)."""

from src.modules.integrations.connectors import builtin as _builtin  # noqa: F401
from src.modules.integrations.connectors import cbr as _cbr  # noqa: F401
