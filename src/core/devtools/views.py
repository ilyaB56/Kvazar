"""Ракурсы ведения (devtools-spec §7): конфигурация + direct-движок.

Direct — генерический CRUD по таблицам белого списка (без бизнес-
инвариентов): валидации ракурса → UPDATE/INSERT через ORM по PK →
record_versions + events_log. Domain — реестр адаптеров модулей
(Manifest.devtools_adapters), исполнение in-process.

Чёрный список (документы двойной записи, секреты, журналы) — создание
ракурса → 422 (§7.3): редактирование документов только через доменные
API (ADR-007).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.devtools.meta import get_table
from src.core.models import AuditEvent, MaintenanceView, User
from src.core.versioning import record_version

# §7.3: создание ракурса на эти таблицы → 422 table_forbidden
MAINT_VIEW_FORBIDDEN = frozenset({
    # документы двойной записи и строки
    "transactions", "stock_moves", "item_serials",
    "purchase_orders", "purchase_order_lines", "receipts", "receipt_lines",
    "sales_orders", "sales_order_lines", "shipments", "shipment_lines",
    "tech_cards", "production_orders",
    # деньги/потоки
    "online_payments", "flow_runs", "webhook_events",
    # секреты и доступы
    "users", "auth_sessions", "revoked_tokens", "password_resets",
    "signup_requests", "user_totp", "totp_backup_codes", "api_tokens",
    "connections", "webhook_endpoints", "settings",
    # журналы и инфраструктура
    "events_log", "record_versions", "event_outbox", "module_registry",
    "backups", "doc_sequences", "sync_runs", "periods",
})

# §7.3: direct-режим только для таблиц без бизнес-инвариентов
DIRECT_ALLOWED = {
    ("mgmt_accounting", "categories"): {
        "context": "org",
        "notes": "parent_id нередактируем в v1 (циклы не проверяем)",
        "non_editable": ("company_id", "id", "internal_code", "parent_id",
                         "created_at", "updated_at"),
    },
    ("mgmt_accounting", "locations"): {
        "context": "org",
        "notes": "обязателен where is_transit = false",
        "guarded_where": [{"col": "is_transit", "op": "eq", "value": False}],
        "non_editable": ("company_id", "id", "created_at"),
    },
    ("mgmt_accounting", "units"): {
        "context": "platform",
        "notes": "глобальный справочник",
        "non_editable": ("id",),
    },
    ("mgmt_accounting", "doc_types"): {
        "context": "platform",
        "notes": "number_prefix влияет на нумерацию",
        "non_editable": ("id",),
    },
}

# domain-режим: таблица → ключ адаптера (регистрирует модуль)
DOMAIN_TABLES = {
    ("mgmt_accounting", "counterparties"): "counterparty",
}


class ViewError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _table_key(schema: str, table: str) -> tuple[str, str]:
    return (schema, table)


def validate_view_definition(schema: str, table: str, mode: str,
                             definition: dict) -> None:
    """Валидация при создании/правке ракурса (§7.3, §7.5)."""
    if table in MAINT_VIEW_FORBIDDEN:
        raise ViewError(
            "table_forbidden",
            f"таблица {schema}.{table} — чёрный список ракурсов "
            "(документы двойной записи / секреты / журналы); правка только "
            "через доменные API")

    if mode == "direct":
        rules = DIRECT_ALLOWED.get(_table_key(schema, table))
        if rules is None:
            raise ViewError(
                "direct_not_allowed",
                f"direct-режим для {schema}.{table} не разрешён (белый список: "
                + ", ".join(f"{s}.{t}" for s, t in DIRECT_ALLOWED) + ")")
        guarded = rules.get("guarded_where")
        if guarded:
            where = definition.get("where") or []
            if not any(
                entry.get("col") == guarded[0]["col"]
                and entry.get("op") == guarded[0]["op"]
                and entry.get("value") == guarded[0]["value"]
                for entry in where if isinstance(entry, dict)):
                raise ViewError(
                    "guarded_filter_required",
                    f"для {schema}.{table} обязателен фиксированный фильтр "
                    f"{guarded[0]['col']} = {guarded[0]['value']}")

    elif mode == "domain":
        if _table_key(schema, table) not in DOMAIN_TABLES:
            raise ViewError(
                "domain_adapter_not_found",
                f"для {schema}.{table} нет доменного адаптера")
    else:
        raise ViewError("invalid_mode", f"режим {mode!r}: domain|direct")

    # колонки определения существуют
    meta = get_table(schema, table)
    for entry in definition.get("columns") or []:
        if isinstance(entry, dict):
            meta.column(str(entry.get("name", "")))


def view_context_rule(schema: str, table: str) -> str:
    """org|platform — допустимый контекст использования."""
    if _table_key(schema, table) in DOMAIN_TABLES:
        return "org"  # контрагенты — org-таблица
    rules = DIRECT_ALLOWED.get(_table_key(schema, table))
    return (rules or {}).get("context", "org")


def check_view_access(view: MaintenanceView, user: User) -> None:
    """access-поле поверх привилегии (§7.1): company | {users:[...]}."""
    access = (view.definition or {}).get("access", "company")
    if isinstance(access, dict):
        allowed = {str(u) for u in access.get("users", [])}
        if str(user.id) not in allowed:
            raise ViewError("view_access_denied",
                            "ракурс ограничен списком пользователей")


def view_visible(view: MaintenanceView, user: User) -> bool:
    try:
        check_view_access(view, user)
        return True
    except ViewError:
        return False


def _editable_columns(view: MaintenanceView) -> list[str]:
    return [c["name"] for c in (view.definition or {}).get("columns", [])
            if isinstance(c, dict) and c.get("editable")]


def _apply_validations(view: MaintenanceView, values: dict) -> None:
    """Декларативные валидации (§7.1): regex/required."""
    import re as _re

    for rule in (view.definition or {}).get("validations", []):
        if not isinstance(rule, dict):
            continue
        column = str(rule.get("col", ""))
        kind = str(rule.get("rule", ""))
        if column not in values:
            continue
        value = values[column]
        if kind == "required" and (value is None or str(value).strip() == ""):
            raise ViewError("validation_failed",
                            f"{column}: обязательное значение")
        if kind == "regex" and value not in (None, ""):
            pattern = str(rule.get("value", ""))
            if not _re.fullmatch(pattern, str(value)):
                raise ViewError(
                    "validation_failed",
                    f"{column}: не соответствует правилу {pattern}")


# ---------- Direct-движок: строки ----------

def _orm_model(schema: str, table: str):
    """ORM-класс по схеме+таблица (Base.registry)."""
    from src.db import Base

    for mapper in Base.registry.mappers:
        if mapper.local_table.name == table and \
                (mapper.local_table.schema or "public") == schema:
            return mapper.class_
    raise ViewError("orm_not_found", f"нет ORM-модели {schema}.{table}")


def direct_rows(db: Session, view: MaintenanceView, *,
                filters: list[dict], sort: list[str],
                limit: int, offset: int,
                org_id: uuid.UUID | None) -> tuple[list[dict], int]:
    """Чтение строк ракурса: where-область + автокомпания + фильтры."""
    from src.core.devtools import browser

    all_filters = list(view.definition.get("where") or []) + list(filters or [])
    visible = [c["name"] for c in view.definition.get("columns", [])
               if isinstance(c, dict) and c.get("visible", True)]
    # PK всегда в выборке: правка строки идёт по нему (§7.4)
    meta = get_table(view.table_schema, view.table_name)
    pk_columns = [c.name for c in meta.columns if c.pk]
    for pk_col in pk_columns:
        if pk_col not in visible:
            visible = [pk_col] + visible
    return browser.fetch_rows(
        view.table_schema, view.table_name, columns=visible or None,
        filters=all_filters, sort=sort or [
            f"{e['col']},{e.get('dir', 'asc')}"
            for e in view.definition.get("order_by", [])],
        limit=limit, offset=offset, org_id=org_id)


def _guard_editable(view: MaintenanceView, values: dict) -> None:
    rules = DIRECT_ALLOWED.get(_table_key(view.table_schema, view.table_name)) or {}
    non_editable = set(rules.get("non_editable", ("id", "company_id")))
    editable = set(_editable_columns(view))
    for column in values:
        if column in non_editable or column not in editable:
            raise ViewError(
                "column_not_editable",
                f"колонка {column} не входит в editable ракурса")
    # company_id автокомпании — нередактируем всегда (§7.2)
    if "company_id" in values:
        raise ViewError(
            "column_not_editable",
            "company_id проставляется автоматически из контекста")


def direct_update(db: Session, view: MaintenanceView, pk: str,
                  values: dict, user: User,
                  org_id: uuid.UUID | None) -> dict:
    """Правка строки direct-ракурса: одна транзакция с версиями/аудитом."""
    _guard_editable(view, values)
    if not values:
        raise ViewError("empty_patch", "нет изменяемых колонок")
    _apply_validations(view, values)

    model = _orm_model(view.table_schema, view.table_name)
    row = db.scalar(select(model).where(model.id == uuid.UUID(str(pk))))
    if row is None:
        raise ViewError("row_not_found", f"строка {pk} не найдена")
    # автокомпания: чужая строка невидима (§7.2)
    if org_id is not None and getattr(row, "company_id", None) is not None \
            and str(row.company_id) != str(org_id):
        raise ViewError("row_not_found", f"строка {pk} не найдена")

    coerced = {}
    meta = get_table(view.table_schema, view.table_name)
    from src.core.devtools.browser import _coerce_value
    for column, value in values.items():
        coerced[column] = _coerce_value(meta, column, value)

    diff = {k: {"old": _plain(getattr(row, k)), "new": _plain(v)}
            for k, v in coerced.items()}
    for key, value in coerced.items():
        setattr(row, key, value)
    record_version(db, view.table_name, row.id, user.id, diff,
                   reason="maintenance_view")
    db.add(AuditEvent(
        user_id=user.id, action="system.view_row_updated",
        entity_type=view.table_name, entity_id=str(row.id),
        payload={"view_id": str(view.id), "changed": sorted(coerced)}))
    db.commit()
    db.refresh(row)
    return _row_dict(row, view)


def direct_create(db: Session, view: MaintenanceView, values: dict,
                  user: User, org_id: uuid.UUID | None) -> dict:
    """Создание строки direct-ракурса: company_id из контекста."""
    _guard_editable(view, values)
    _apply_validations(view, values)

    model = _orm_model(view.table_schema, view.table_name)
    meta = get_table(view.table_schema, view.table_name)
    from src.core.devtools.browser import _coerce_value

    fields = {}
    for column, value in values.items():
        fields[column] = _coerce_value(meta, column, value)
    # автокомпания для org-таблиц (§7.2)
    if meta.company_scoped:
        if org_id is None:
            raise ViewError(
                "platform_context_required",
                "таблица org-контекста: создайте ракурс внутри организации")
        fields["company_id"] = org_id

    # обязательные NOT NULL колонки без default
    for column in meta.columns:
        if column.name not in fields and not column.nullable \
                and not column.pk and column.name != "company_id":
            default = _column_default(db, view, column.name)
            if default is None:
                raise ViewError(
                    "required_column_missing",
                    f"обязательная колонка {column.name} не задана")
            fields[column.name] = default

    row = model(**fields)
    db.add(row)
    db.flush()
    db.add(AuditEvent(
        user_id=user.id, action="system.view_row_created",
        entity_type=view.table_name, entity_id=str(row.id),
        payload={"view_id": str(view.id)}))
    db.commit()
    db.refresh(row)
    return _row_dict(row, view)


def _column_default(db: Session, view: MaintenanceView, column: str):
    """Дефолт для NOT NULL без значения: internal_code → следующий номер."""
    if view.table_name == "counterparties" and column == "internal_code":
        from src.modules.mgmt_accounting import service
        return service.next_counterparty_code(db)
    if column in ("is_active",):
        return True
    if column.endswith("_at"):
        return None  # server_default проставит
    return None


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _row_dict(row: Any, view: MaintenanceView) -> dict:
    visible = [c["name"] for c in view.definition.get("columns", [])
               if isinstance(c, dict) and c.get("visible", True)]
    meta = get_table(view.table_schema, view.table_name)
    out = {}
    for name in (visible or [c.name for c in meta.columns]):
        value = getattr(row, name, None)
        out[name] = "***" if name in meta.masked else _plain(value)
    return out


# ---------- Domain-режим: реестр адаптеров ----------

def domain_adapter(key: str) -> dict:
    """Адаптер модуля из реестра манифестов (§7.2)."""
    from src.core.plugins import MANIFESTS

    for manifest in MANIFESTS:
        adapters = getattr(manifest, "devtools_adapters", None) or {}
        if key in adapters:
            return adapters[key]
    raise ViewError("domain_adapter_not_found", f"нет адаптера {key}")
