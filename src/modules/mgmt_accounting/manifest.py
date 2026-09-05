"""Манифест модуля mgmt_accounting — точка регистрации в ядре.

API живёт под /api/v1/accounting (url_prefix), имя модуля — mgmt_accounting.
"""

from __future__ import annotations

from src.core.contracts import Manifest
from src.modules.mgmt_accounting.features.inventory.router import router as inventory_router
from src.modules.mgmt_accounting.features.purchasing.router import router as purchasing_router
from src.modules.mgmt_accounting.features.sales.router import router as sales_router
from src.modules.mgmt_accounting.router import router
from src.modules.mgmt_accounting.service import upsert_rates_from_event

manifest = Manifest(
    name="mgmt_accounting",
    version="0.1.0",
    db_schema="mgmt_accounting",
    depends_on=("core",),
    routers=(router, inventory_router, purchasing_router, sales_router),
    url_prefix="accounting",
    event_handlers={
        # курсы от коннекторов (showcase-chain, этап C) → upsert в rates
        "integration.rates.fetched": upsert_rates_from_event,
    },
)
