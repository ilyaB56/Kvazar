"""Манифест модуля ai_agent — точка регистрации в ядре."""

from __future__ import annotations

from src.core.contracts import Manifest
from src.modules.ai_agent.router import router

manifest = Manifest(
    name="ai_agent",
    version="0.1.0",
    db_schema="ai_agent",
    depends_on=("core", "integrations", "mgmt_accounting"),
    routers=(router,),
    url_prefix="ai",
)
