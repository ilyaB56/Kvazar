"""API ракурсов ведения (devtools-spec §7.4).

CRUD конфигурации (аудит + record_versions на каждое изменение) +
строки (чтение ro; правка/создание rw): direct-движок или доменный
адаптер модуля (реестр Manifest.devtools_adapters).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import User, require_module
from src.core.devtools import views as mv
from src.core.devtools.meta import TableBrowserError, get_table
from src.core.models import AuditEvent, MaintenanceView
from src.core.versioning import record_version
from src.db import get_db

router = APIRouter(tags=["system"])


def _require_views(user: User = Depends(require_module("maint_views", "ro"))):
    return user


def _require_views_rw(user: User = Depends(require_module("maint_views"))):
    return user


def _org_context(user: User) -> uuid.UUID | None:
    org = getattr(user, "token_org", None)
    return uuid.UUID(str(org)) if org else None


def _get_view(db: Session, view_id: uuid.UUID, user: User) -> MaintenanceView:
    view = db.get(MaintenanceView, view_id)
    if view is None or not view.is_active:
        raise HTTPException(404, "View not found")
    try:
        mv.check_view_access(view, user)
    except mv.ViewError as exc:
        raise HTTPException(403, exc.detail) from exc
    return view


def _ve(exc: Exception) -> HTTPException:
    if isinstance(exc, (mv.ViewError, TableBrowserError)):
        return HTTPException(422, f"{exc.code if hasattr(exc, 'code') else 'error'}: {exc}")
    raise exc


# ---------- CRUD конфигурации ----------

class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    table_schema: str = Field(max_length=63)
    table_name: str = Field(max_length=63)
    mode: str = Field(pattern="^(domain|direct)$")
    definition: dict
    is_active: bool = True


class ViewPatch(BaseModel):
    name: str | None = None
    definition: dict | None = None
    is_active: bool | None = None


def _view_out(view: MaintenanceView, user: User, rw: bool) -> dict:
    return {
        "id": str(view.id), "name": view.name,
        "table_schema": view.table_schema, "table_name": view.table_name,
        "mode": view.mode, "is_active": view.is_active,
        "is_template": view.company_id is None,
        # definition — только rw (§7.4)
        **({"definition": view.definition} if rw else {}),
        "updated_at": view.updated_at or view.created_at,
    }


def _user_rw(db: Session, user: User) -> bool:
    from src.core.auth import module_level

    return module_level(db, user.role, "maint_views") == "rw" or user.role == "admin"


@router.get("/system/maintenance-views")
def list_views(user: User = Depends(_require_views),
              db: Session = Depends(get_db)):
    """Свои + платформенные шаблоны; definition — только rw."""
    org = _org_context(user)
    rw = _user_rw(db, user)
    query = select(MaintenanceView).where(MaintenanceView.is_active.is_(True))
    if org is not None:
        from sqlalchemy import or_
        query = query.where(or_(
            MaintenanceView.company_id == org,
            MaintenanceView.company_id.is_(None)))
    elif not user.is_platform_admin if hasattr(user, "is_platform_admin") else False:
        pass
    rows = db.scalars(query.order_by(MaintenanceView.name)).all()
    return [_view_out(row, user, rw) for row in rows if mv.view_visible(row, user)]


@router.post("/system/maintenance-views", status_code=201)
def create_view(body: ViewIn, user: User = Depends(_require_views_rw),
                db: Session = Depends(get_db)):
    """Создать ракурс (валидация списков §7.3). company_id — из контекста
    принудительно; платформенный шаблон — только платформенный контекст."""
    org = _org_context(user)
    try:
        get_table(body.table_schema, body.table_name)  # 422 при неизвестной
        mv.validate_view_definition(
            body.table_schema, body.table_name, body.mode, body.definition)
        context = mv.view_context_rule(body.table_schema, body.table_name)
        if context == "platform" and org is not None:
            raise mv.ViewError(
                "platform_context_required",
                f"{body.table_schema}.{body.table_name} — платформенная "
                "таблица: ракурс создаётся платформенным контекстом")
        if org is None and context == "org":
            raise mv.ViewError(
                "org_context_required",
                "org-таблица: создайте ракурс внутри организации (select-org)")
        definition = {**body.definition, "access": body.definition.get("access", "company")}
        view = MaintenanceView(
            company_id=org,  # NULL = шаблон (только платформа)
            name=body.name.strip(), table_schema=body.table_schema,
            table_name=body.table_name, mode=body.mode,
            definition=definition, is_active=body.is_active,
            created_by=user.id)
        db.add(view)
        db.flush()
        record_version(db, "maintenance_view", view.id, user.id,
                       {"created": {"name": view.name, "table":
                        f"{view.table_schema}.{view.table_name}"}},
                       reason="maintenance_view")
        db.add(AuditEvent(user_id=user.id, action="system.view.created",
                          entity_type="maintenance_view", entity_id=str(view.id),
                          payload={"name": view.name,
                                   "table": f"{view.table_schema}.{view.table_name}"}))
        db.commit()
        db.refresh(view)
        return _view_out(view, user, True)
    except (mv.ViewError, TableBrowserError) as exc:
        raise _ve(exc) from exc


@router.patch("/system/maintenance-views/{view_id}")
def patch_view(view_id: uuid.UUID, body: ViewPatch,
               user: User = Depends(_require_views_rw),
               db: Session = Depends(get_db)):
    """Изменить определение/активность. Своей org — rw; шаблон — платформа."""
    view = _get_view(db, view_id, user)
    org = _org_context(user)
    if view.company_id is None and org is not None:
        raise HTTPException(403, "Платформенный шаблон правит только платформа")
    if view.company_id is not None and str(view.company_id) != str(org):
        raise HTTPException(404, "View not found")

    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not changes:
        raise HTTPException(422, "Пустая правка")
    try:
        if "definition" in changes:
            definition = changes["definition"]
            get_table(view.table_schema, view.table_name)
            mv.validate_view_definition(
                view.table_schema, view.table_name, view.mode, definition)
            changes["definition"] = {
                **definition, "access": definition.get("access", "company")}
        diff = {key: {"old": _plain_of(view, key),
                      "new": _plain_value(value)}
                for key, value in changes.items()}
        for key, value in changes.items():
            setattr(view, key, value if key != "name" else str(value).strip())
        view.updated_at = datetime.now(UTC)
        record_version(db, "maintenance_view", view.id, user.id, diff,
                       reason="maintenance_view")
        db.add(AuditEvent(user_id=user.id, action="system.view.updated",
                          entity_type="maintenance_view", entity_id=str(view.id),
                          payload={"changed": sorted(changes)}))
        db.commit()
        db.refresh(view)
        return _view_out(view, user, True)
    except (mv.ViewError, TableBrowserError) as exc:
        raise _ve(exc) from exc


def _plain_of(view: MaintenanceView, key: str):
    value = getattr(view, key)
    return value if not isinstance(value, dict) else {"<definition>": "…"}


def _plain_value(value):
    return value if not isinstance(value, dict) else {"<definition>": "…"}


@router.delete("/system/maintenance-views/{view_id}")
def delete_view(view_id: uuid.UUID, user: User = Depends(_require_views_rw),
                db: Session = Depends(get_db)):
    """Удалить конфигурацию (данные не трогаются)."""
    view = _get_view(db, view_id, user)
    org = _org_context(user)
    if view.company_id is None and org is not None:
        raise HTTPException(403, "Платформенный шаблон удаляет только платформа")
    if view.company_id is not None and str(view.company_id) != str(org):
        raise HTTPException(404, "View not found")
    db.delete(view)
    db.add(AuditEvent(user_id=user.id, action="system.view.deleted",
                      entity_type="maintenance_view", entity_id=str(view.id),
                      payload={"name": view.name}))
    db.commit()
    return {"ok": True}


# ---------- Строки ракурса ----------

@router.get("/system/maintenance-views/{view_id}/rows")
def view_rows(view_id: uuid.UUID,
              columns: str | None = None,
              filters: str | None = None,
              sort: str | None = None,
              limit: int = 50, offset: int = 0,
              user: User = Depends(_require_views),
              db: Session = Depends(get_db)):
    """Строки ракурса: where-область + автокомпания + фильтры (§6.3)."""
    import json as _json

    view = _get_view(db, view_id, user)
    try:
        org = _org_context(user)
        context = mv.view_context_rule(view.table_schema, view.table_name)
        if context == "platform" and org is not None:
            raise mv.ViewError(
                "platform_context_required",
                "платформенный ракурс — используйте платформенный контекст")
        if view.mode == "domain":
            adapter = mv.domain_adapter(
                mv.DOMAIN_TABLES[(view.table_schema, view.table_name)])
            rows, total = adapter["list"](
                db, org_id=org, limit=limit, offset=offset)
            return {"items": rows, "total": total}
        filter_list = _json.loads(filters) if filters else []
        sort_list = [s.strip() for s in sort.split("|")] if sort else []
        rows, total = mv.direct_rows(
            db, view, filters=filter_list, sort=sort_list,
            limit=limit, offset=offset, org_id=org)
        return {"items": rows, "total": total}
    except (mv.ViewError, TableBrowserError) as exc:
        raise _ve(exc) from exc
    except _json.JSONDecodeError as exc:
        raise HTTPException(422, f"invalid_filters: {exc}") from exc


@router.patch("/system/maintenance-views/{view_id}/rows/{pk}")
def view_row_update(view_id: uuid.UUID, pk: str, body: dict,
                    user: User = Depends(_require_views_rw),
                    db: Session = Depends(get_db)):
    """Правка строки: direct-движок или доменный адаптер (§7.2)."""
    view = _get_view(db, view_id, user)
    org = _org_context(user)
    try:
        if view.mode == "domain":
            adapter = mv.domain_adapter(
                mv.DOMAIN_TABLES[(view.table_schema, view.table_name)])
            return adapter["update"](db, pk=pk, values=body, user=user,
                                     org_id=org)
        return mv.direct_update(db, view, pk, body, user, org)
    except (mv.ViewError, TableBrowserError) as exc:
        raise _ve(exc) from exc


@router.post("/system/maintenance-views/{view_id}/rows", status_code=201)
def view_row_create(view_id: uuid.UUID, body: dict,
                    user: User = Depends(_require_views_rw),
                    db: Session = Depends(get_db)):
    """Создание строки: company_id автокомпании (direct) или домен."""
    view = _get_view(db, view_id, user)
    org = _org_context(user)
    try:
        if view.mode == "domain":
            adapter = mv.domain_adapter(
                mv.DOMAIN_TABLES[(view.table_schema, view.table_name)])
            return adapter["create"](db, values=body, user=user, org_id=org)
        return mv.direct_create(db, view, body, user, org)
    except (mv.ViewError, TableBrowserError) as exc:
        raise _ve(exc) from exc
