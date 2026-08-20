"""Манифест модуля mgmt_accounting — точка регистрации в ядре.

API живёт под /api/v1/accounting (url_prefix), имя модуля — mgmt_accounting.
"""

from __future__ import annotations

from src.core.contracts import Manifest
from src.modules.mgmt_accounting.router import router

manifest = Manifest(
    name="mgmt_accounting",
    version="0.1.0",
    db_schema="mgmt_accounting",
    depends_on=("core",),
    routers=(router,),
    url_prefix="accounting",
)
