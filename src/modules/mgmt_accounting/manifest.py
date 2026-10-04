"""Манифест модуля mgmt_accounting — точка регистрации в ядре.

API живёт под /api/v1/accounting (url_prefix), имя модуля — mgmt_accounting.
"""

from __future__ import annotations

from src.core.contracts import Manifest
from src.modules.mgmt_accounting.features.inventory.router import router as inventory_router
from src.modules.mgmt_accounting.features.purchasing.router import router as purchasing_router
from src.modules.mgmt_accounting.features.production.router import router as production_router
from src.modules.mgmt_accounting.features.sales.router import router as sales_router
from src.modules.mgmt_accounting.router import router
from src.modules.mgmt_accounting.service import upsert_rates_from_event

def counterparty_adapter():
    """Доменный адаптер ракурсов ведения (devtools §7.2): list/create/
    update через сервисный слой модуля in-process; правка — с доменной
    валидацией дублей ИНН+КПП, версиями и аудитом."""

    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from src.core.models import AuditEvent, User
    from src.modules.mgmt_accounting import models as m
    from src.modules.mgmt_accounting.router import CounterpartyOut

    def list_rows(db: Session, *, org_id, limit: int, offset: int):
        query = select(m.Counterparty).order_by(m.Counterparty.name)
        if org_id is not None:
            query = query.where(m.Counterparty.company_id == org_id)
        total = db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())) or 0
        rows = db.scalars(query.limit(limit).offset(offset)).all()
        return ([CounterpartyOut.model_validate(r).model_dump(
            exclude={"warning"}) for r in rows], int(total))

    def create_row(db: Session, *, values: dict, user: User, org_id):
        from src.modules.mgmt_accounting import service

        if org_id is None:
            raise ValueError("org_context_required: создайте внутри организации")
        data = {k: str(v).strip() for k, v in values.items()
                if k in ("name", "inn", "kpp")}
        counterparty, _warning = service.create_counterparty(
            db, {"name": data.get("name", ""), "inn": data.get("inn", ""),
                 "kpp": data.get("kpp", "")}, company_id=org_id)
        db.add(AuditEvent(user_id=user.id, action="system.view_row_created",
                          entity_type="counterparty",
                          entity_id=str(counterparty.id),
                          payload={"via": "maintenance_view"}))
        db.commit()
        db.refresh(counterparty)
        return CounterpartyOut.model_validate(counterparty).model_dump(
            exclude={"warning"})

    def update_row(db: Session, *, pk: str, values: dict, user: User, org_id):
        from src.modules.mgmt_accounting.router import patch_counterparty

        class _Body:
            def model_dump(self, exclude_unset=True):
                return {k: v for k, v in values.items()
                        if k in ("name", "inn", "kpp", "is_active")}

        # правка через доменный PATCH-слой (валидация дублей внутри)
        result = patch_counterparty(
            uuid.UUID(str(pk)), _Body(), user=user, db=db,
            scoped=org_id)
        return result.model_dump(exclude={"warning"})             if hasattr(result, "model_dump") else result

    import uuid  # noqa: E402

    return {"list": list_rows, "create": create_row, "update": update_row}


manifest = Manifest(
    name="mgmt_accounting",
    version="0.1.0",
    db_schema="mgmt_accounting",
    depends_on=("core",),
    routers=(router, inventory_router, purchasing_router, sales_router, production_router),
    url_prefix="accounting",
    event_handlers={
        # курсы от коннекторов (showcase-chain, этап C) → upsert в rates
        "integration.rates.fetched": upsert_rates_from_event,
    },
    # доменный адаптер ракурсов ведения (devtools §7.2)
    devtools_adapters={"counterparty": counterparty_adapter()},
)
