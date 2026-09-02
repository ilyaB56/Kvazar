"""Манифест модуля mini_crm — точка регистрации в ядре."""

from __future__ import annotations

from src.core.contracts import Manifest
from src.modules.mini_crm.router import router

manifest = Manifest(
    name="mini_crm",
    version="0.1.0",
    db_schema="mini_crm",
    depends_on=("core",),
    routers=(router,),
    url_prefix="crm",
)
