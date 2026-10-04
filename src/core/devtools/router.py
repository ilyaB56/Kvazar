"""API браузера таблиц (devtools-spec §6.2) + личные пресеты (О2).

Эндпоинты ядра: только ядру доступен реестр всех схем. Чтение — по
праву table_browser:ro; чтение не журналируется (решение О4/О6),
журналируется только экспорт (system.table_exported).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import User, require_module
from src.core.devtools import browser
from src.core.devtools.meta import TableBrowserError, rebuild_cache, tables_cache
from src.core.models import AuditEvent, TablePreset
from src.db import get_db
from src.config import get_settings

router = APIRouter(tags=["system"])


def _require_browser(user: User = Depends(require_module("table_browser", "ro"))):
    return user


def _org_context(user: User) -> uuid.UUID | None:
    """org из JWT; None = платформенный контекст (супер-право, §6.3)."""
    org = getattr(user, "token_org", None)
    return uuid.UUID(str(org)) if org else None


def _tb_error(exc: TableBrowserError) -> HTTPException:
    return HTTPException(422, f"{exc.code}: {exc.detail}")


# ---------- Метаданные (§6.2 п.1–2) ----------

@router.get("/system/tables")
def list_tables(user: User = Depends(_require_browser)):
    """Список таблиц с краткими метаданными. Платформенные таблицы
    (companies, signup_requests) в org-контексте не возвращаются."""
    org = _org_context(user)
    out = []
    for meta in sorted(tables_cache().values(), key=lambda m: (m.module, m.table)):
        if meta.platform_only and org is not None:
            continue
        out.append({
            "schema": meta.schema, "table": meta.table,
            "module": meta.module, "title": meta.title,
            "company_scoped": meta.company_scoped,
            "platform_only": meta.platform_only,
            "masked": meta.masked,
        })
    return out


@router.get("/system/tables/{schema}/{table}")
def table_meta(schema: str, table: str,
               user: User = Depends(_require_browser)):
    try:
        meta = tables_cache().get(f"{schema}.{table}")
        if meta is None:
            raise TableBrowserError("unknown_table", f"нет таблицы {schema}.{table}")
        if meta.platform_only and _org_context(user) is not None:
            raise TableBrowserError(
                "platform_only", f"таблица {schema}.{table} доступна только платформе")
    except TableBrowserError as exc:
        raise _tb_error(exc) from exc
    return {
        "schema": meta.schema, "table": meta.table, "module": meta.module,
        "title": meta.title, "company_scoped": meta.company_scoped,
        "platform_only": meta.platform_only, "masked": meta.masked,
        "columns": [
            {"name": c.name, "type": c.type, "nullable": c.nullable,
             "pk": c.pk, "fk": c.fk} for c in meta.columns],
    }


@router.post("/system/tables/rebuild")
def tables_rebuild(user: User = Depends(require_module("table_browser", "rw"))):
    """Перестройка метакэша (платформенный rw — горячее добавление модуля)."""
    return {"tables": rebuild_cache()}


# ---------- Строки (§6.2 п.3) ----------

class RowsParams(BaseModel):
    columns: list[str] | None = None
    filters: list[dict] = Field(default_factory=list)
    sort: list[str] = Field(default_factory=list,
                             description="['col,asc', 'col,desc'] — максимум 2")


@router.get("/system/tables/{schema}/{table}/rows")
def table_rows(schema: str, table: str,
               columns: str | None = None,
               filters: str | None = None,
               sort: str | None = None,
               limit: int = 50, offset: int = 0, fmt: str | None = None,
               user: User = Depends(_require_browser)):
    """Строки таблицы: фильтры/сортировка JSON-параметрами (GET), конверт
    {items, total} (PageParams-конвенция). Чтение через engine_ro."""
    try:
        meta = tables_cache().get(f"{schema}.{table}")
        if meta is None:
            raise TableBrowserError("unknown_table", f"нет таблицы {schema}.{table}")
        if meta.platform_only and _org_context(user) is not None:
            raise TableBrowserError(
                "platform_only", f"таблица {schema}.{table} доступна только платформе")
        column_list = [c.strip() for c in columns.split(",")] if columns else None
        filter_list = json.loads(filters) if filters else []
        sort_list = [s.strip() for s in sort.split("|")] if sort else []
        rows, total = browser.fetch_rows(
            schema, table, columns=column_list, filters=filter_list,
            sort=sort_list, limit=limit, offset=offset,
            org_id=_org_context(user))
    except TableBrowserError as exc:
        raise _tb_error(exc) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"invalid_params: {exc}") from exc
    return {"items": rows, "total": total}


# ---------- Экспорт CSV/XLSX (§6.2 п.4, решение О1) ----------

class ExportIn(BaseModel):
    columns: list[str] | None = None
    filters: list[dict] = Field(default_factory=list)
    sort: list[str] = Field(default_factory=list)
    fmt: str = Field(default="csv", pattern="^(csv|xlsx)$")


@router.post("/system/tables/{schema}/{table}/export")
def table_export(schema: str, table: str, body: ExportIn,
                 user: User = Depends(_require_browser),
                 db: Session = Depends(get_db)):
    """CSV (; UTF-8 BOM) или XLSX (О1). Лимит TABLE_EXPORT_ROW_LIMIT:
    превышение → 422 с total; каждая выгрузка — аудит
    system.table_exported (чтение НЕ журналируется — О4/О6)."""
    settings = get_settings()
    try:
        meta = tables_cache().get(f"{schema}.{table}")
        if meta is None:
            raise TableBrowserError("unknown_table", f"нет таблицы {schema}.{table}")
        if meta.platform_only and _org_context(user) is not None:
            raise TableBrowserError(
                "platform_only", f"таблица {schema}.{table} доступна только платформе")
        filename, content, rows_count = browser.export_rows(
            schema, table, columns=body.columns, filters=body.filters,
            sort=body.sort, row_limit=settings.table_export_row_limit,
            org_id=_org_context(user), fmt=body.fmt)
    except TableBrowserError as exc:
        raise _tb_error(exc) from exc

    org = getattr(user, "token_org", None)
    db.add(AuditEvent(
        user_id=user.id, action="system.table_exported",
        entity_type="table", entity_id=f"{schema}.{table}",
        payload={
            "filters": body.filters[:20], "rows": rows_count,
            "format": body.fmt,
            **({"platform": True} if org is None else {"company_id": str(org)}),
        }))
    db.commit()

    media = ("text/csv" if body.fmt == "csv"
             else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- Личные пресеты (решение владельца О2) ----------

class PresetIn(BaseModel):
    definition: dict = Field(description="{filters, sort, columns}")


@router.get("/system/tables/{schema}/{table}/presets")
def list_presets(schema: str, table: str,
                   user: User = Depends(_require_browser),
                   db: Session = Depends(get_db)):
    """Пресеты пользователя для таблицы (только свои — О2)."""
    rows = db.scalars(select(TablePreset).where(
        TablePreset.user_id == user.id,
        TablePreset.schema_name == schema,
        TablePreset.table_name == table
    ).order_by(TablePreset.name)).all()
    return [{
        "id": str(row.id), "name": row.name, "definition": row.definition,
        "updated_at": row.updated_at or row.created_at,
    } for row in rows]


@router.post("/system/tables/{schema}/{table}/presets", status_code=201)
def save_preset(schema: str, table: str, name: str, body: PresetIn,
                user: User = Depends(_require_browser),
                db: Session = Depends(get_db)):
    """Сохранить/перезаписать пресет под именем (key-value на юзера)."""
    if tables_cache().get(f"{schema}.{table}") is None:
        raise HTTPException(422, f"unknown_table: нет таблицы {schema}.{table}")
    if not name.strip() or len(name) > 200:
        raise HTTPException(422, "invalid_name")
    row = db.scalar(select(TablePreset).where(
        TablePreset.user_id == user.id,
        TablePreset.schema_name == schema,
        TablePreset.table_name == table,
        TablePreset.name == name.strip()))
    if row is None:
        row = TablePreset(user_id=user.id, schema_name=schema,
                           table_name=table, name=name.strip())
        db.add(row)
    row.definition = body.definition
    row.updated_at = datetime.now(UTC)
    db.commit()
    return {"id": str(row.id), "name": row.name, "definition": row.definition}


@router.delete("/system/tables/{schema}/{table}/presets/{preset_id}")
def delete_preset(schema: str, table: str, preset_id: uuid.UUID,
                  user: User = Depends(_require_browser),
                  db: Session = Depends(get_db)):
    row = db.get(TablePreset, preset_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Preset not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
