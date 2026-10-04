"""Запросы браузера таблиц (devtools-spec §6.2–6.3).

Только параметризованный SQL (text() + bound params, §9.3); имена
сверяются с метакэшем ДО построения; чтение — через engine_ro (§9.2).
Автокомпания: company_scoped + org-контекст → неявный company_id = org;
явный фильтр по company_id в org-контексте → 422 (§6.3).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from src.core.devtools.meta import TableBrowserError, TableMeta, get_table
from src.db import engine_ro

OPERATORS = ("eq", "ne", "contains", "in", "between", "is_null", "not_null")
MAX_SORT_LEVELS = 2
COMPANY_COLUMN = "company_id"


def _quote_ident(name: str) -> str:
    """Имя колонки/таблицы в SQL: только после сверки с кэшем; двойные
    кавычки с экранированием — защита в глубину (§9.3)."""
    return '"' + name.replace('"', '""') + '"'


def _coerce_value(meta: TableMeta, column: str, value: Any) -> Any:
    """Приведение значения к типу колонки (§6.2): UUID/дата/число/булево;
    NUMERIC — Decimal (ADR-003)."""
    if value is None:
        return None
    target = meta.python_type(column)
    if target is None:
        return value
    try:
        if target is bool:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "t", "1", "yes")
        if target is Decimal:
            return Decimal(str(value))
        if target is uuid.UUID:
            return uuid.UUID(str(value))
        if target is datetime:
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if target is date:
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        return target(value)
    except (ValueError, TypeError) as exc:
        raise TableBrowserError(
            "invalid_filter_value",
            f"колонка {column}: значение {value!r} не приводится к {target.__name__}") from exc


def _check_masked(meta: TableMeta, column: str) -> None:
    if column in meta.masked:
        raise TableBrowserError(
            "masked_column", f"колонка {column} маскируется — фильтр/сортировка запрещены")


def build_where(meta: TableMeta, filters: list[dict], org_id: uuid.UUID | None) -> tuple[str, dict]:
    """WHERE из фильтров + автокомпания. Возвращает (sql, params).

    org_id не None → company_scoped таблица получает неявный
    company_id = org; явный фильтр по company_id при этом — 422.
    Платформенный контекст (org None): явный фильтр разрешён."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    for raw in filters or []:
        if not isinstance(raw, dict):
            raise TableBrowserError("invalid_filter", "фильтр должен быть объектом")
        column = str(raw.get("col", ""))
        operator = str(raw.get("op", ""))
        meta.column(column)  # 422 при неизвестной (инъекции)
        _check_masked(meta, column)
        if operator not in OPERATORS:
            raise TableBrowserError(
                "invalid_operator", f"оператор {operator!r} не поддерживается")
        if column == COMPANY_COLUMN and org_id is not None:
            raise TableBrowserError(
                "company_filter_forbidden",
                "company_id — служебная колонка области; фильтр по ней "
                "в контексте организации запрещён")

        pname = f"p{len(params)}"
        if operator in ("eq", "ne"):
            params[pname] = _coerce_value(meta, column, raw.get("value"))
            clauses.append(f"{_quote_ident(column)} "
                           f"{'=' if operator == 'eq' else '<>'} :{pname}")
        elif operator == "contains":
            # ILIKE %v%; для не-текстовых колонок — приведение к строке
            params[pname] = f"%{raw.get('value')}%"
            clauses.append(f"{_quote_ident(column)}::text ILIKE :{pname}")
        elif operator == "in":
            values = raw.get("value")
            if not isinstance(values, list) or not values:
                raise TableBrowserError("invalid_filter_value", "in требует непустой массив")
            names = []
            for i, item in enumerate(values):
                sub = f"{pname}_{i}"
                params[sub] = _coerce_value(meta, column, item)
                names.append(f":{sub}")
            clauses.append(f"{_quote_ident(column)} IN ({', '.join(names)})")
        elif operator == "between":
            pair = raw.get("value")
            if not isinstance(pair, list) or len(pair) != 2:
                raise TableBrowserError("invalid_filter_value", "between требует [от, до]")
            params[f"{pname}_a"] = _coerce_value(meta, column, pair[0])
            params[f"{pname}_b"] = _coerce_value(meta, column, pair[1])
            clauses.append(f"{_quote_ident(column)} BETWEEN :{pname}_a AND :{pname}_b")
        elif operator == "is_null":
            clauses.append(f"{_quote_ident(column)} IS NULL")
        elif operator == "not_null":
            clauses.append(f"{_quote_ident(column)} IS NOT NULL")

    # автокомпания (§6.3): неявный фильтр области организации
    if org_id is not None and meta.company_scoped:
        params["__org"] = str(org_id)
        clauses.append(f"{_quote_ident(COMPANY_COLUMN)} = :__org")

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def build_order(meta: TableMeta, sort: list[str]) -> str:
    """ORDER BY из ['col,asc', 'col,desc'] — максимум 2 уровня."""
    if not sort:
        return ""
    parts = []
    for chunk in sort[:MAX_SORT_LEVELS]:
        column, _, direction = chunk.partition(",")
        meta.column(column.strip())
        _check_masked(meta, column.strip())
        direction = direction.strip().lower() or "asc"
        if direction not in ("asc", "desc"):
            raise TableBrowserError("invalid_sort", f"направление {direction!r}")
        parts.append(f"{_quote_ident(column.strip())} {direction.upper()}")
    return " ORDER BY " + ", ".join(parts)


def _serialize(value: Any) -> Any:
    """Значение → JSON-типы: Decimal строкой (ADR-003), datetime ISO,
    UUID строкой, маскируемые — до вызывающего."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def fetch_rows(schema: str, table: str, *, columns: list[str] | None,
               filters: list[dict], sort: list[str],
               limit: int, offset: int,
               org_id: uuid.UUID | None) -> tuple[list[dict], int]:
    """Строки таблицы через engine_ro. Возвращает (rows, total)."""
    meta = get_table(schema, table)
    selected = columns or [c.name for c in meta.columns]
    for column in selected:
        meta.column(column)

    where, params = build_where(meta, filters, org_id)
    order = build_order(meta, sort)
    select_list = ", ".join(_quote_ident(c) for c in selected)
    base = (f"SELECT {select_list} FROM "
            f"{_quote_ident(meta.schema)}.{_quote_ident(meta.table)}{where}")

    with engine_ro.connect() as connection:
        total = connection.execute(
            text(f"SELECT count(*) FROM "
                 f"{_quote_ident(meta.schema)}.{_quote_ident(meta.table)}{where}"),
            params).scalar() or 0
        query = text(base + order + " LIMIT :__limit OFFSET :__offset")
        result = connection.execute(
            query, {**params, "__limit": limit, "__offset": offset})

        rows = []
        for row in result.mappings():
            item = {}
            for column in selected:
                value = row[column]
                item[column] = "***" if column in meta.masked else _serialize(value)
            rows.append(item)
    return rows, int(total)


def export_rows(schema: str, table: str, *, columns: list[str] | None,
                filters: list[dict], sort: list[str],
                row_limit: int, org_id: uuid.UUID | None,
                fmt: str = "csv") -> tuple[str, bytes, int]:
    """Выгрузка таблицы: CSV (; + BOM, §6.2) или XLSX (решение О1).

    Возвращает (filename, content, rows_count). Превышение row_limit →
    TableBrowserError('export_limit') с total — вызывающий отдаёт 422
    (проверка ДО выгрузки и повторно здесь, §6.2)."""
    meta = get_table(schema, table)
    # выгружаем всё до лимита (без пагинации), серверная проверка лимита
    rows, total = fetch_rows(schema, table, columns=columns,
                             filters=filters, sort=sort,
                             limit=row_limit + 1, offset=0, org_id=org_id)
    if total > row_limit:
        raise TableBrowserError(
            "export_limit",
            f"выгрузка {total} строк превышает лимит {row_limit} — уточните фильтр")
    rows = rows[:row_limit]
    selected = columns or [c.name for c in meta.columns]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    if fmt == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(meta.table[:31])
        sheet.append(selected)
        for row in rows:
            sheet.append([row.get(c) for c in selected])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return (f"{schema}__{table}__{stamp}.xlsx", buffer.getvalue(), len(rows))

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(selected)
    for row in rows:
        writer.writerow([
            "" if row.get(c) is None else str(row.get(c)) for c in selected])
    # UTF-8 с BOM: Excel открывает кириллицу без танцев (§6.2)
    return (f"{schema}__{table}__{stamp}.csv",
            ("\ufeff" + buffer.getvalue()).encode("utf-8"), len(rows))
