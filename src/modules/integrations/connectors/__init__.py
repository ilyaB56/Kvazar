"""Коннекторы интеграций — единственная точка сети (ADR-001).
Импорт модулей регистрирует их в общем registry (sdk)."""

from src.modules.integrations.connectors import builtin as _builtin  # noqa: F401
from src.modules.integrations.connectors import acquiring as _acquiring  # noqa: F401
from src.modules.integrations.connectors import cbr as _cbr  # noqa: F401
from src.modules.integrations.connectors import llm as _llm  # noqa: F401
from src.modules.integrations.connectors import telegram as _telegram  # noqa: F401
