"""Метакэш таблиц для браузера (devtools-spec §6.1).

Строится из Base.metadata (единый реестр всех ORM-моделей): схема,
таблица, колонки с типами, маскируемые (§9.1), company_scoped,
platform_only, title (первая строка docstring модели), module
(схема → модуль из MANIFESTS). Перестраивается по требованию
эндпоинтом платформенного контекста (горячее добавление модуля).

Идентификаторы (схема/таблица/колонка) сверяются с кэшем ДО построения
SQL — несовпадение → TableBrowserError(422) (§9.3, инъекции).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from src.db import Base


class TableBrowserError(ValueError):
    """422 браузера таблиц: неизвестный идентификатор, запрещённый
    фильтр/сортировка, лимит экспорта."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# паттерны маскируемых колонок (владелец, §9.1): значение всегда ***,
# фильтр/сортировка по ним — 422 (оракул побайтового подбора)
MASKED_PATTERNS = ("*_enc", "password*", "token*", "secret*", "totp_*")

# таблицы платформы (§6.1): видны только в платформенном контексте
PLATFORM_ONLY = {"erp_core.companies", "erp_core.signup_requests"}


@dataclass
class ColumnMeta:
    name: str
    type: str
    nullable: bool
    pk: bool = False
    fk: str | None = None


@dataclass
class TableMeta:
    schema: str
    table: str
    module: str
    title: str
    company_scoped: bool
    platform_only: bool
    columns: list[ColumnMeta] = field(default_factory=list)
    masked: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.table}"

    def column(self, name: str) -> ColumnMeta:
        for col in self.columns:
            if col.name == name:
                return col
        raise TableBrowserError(
            "unknown_column", f"нет колонки {name} в {self.key}")

    def python_type(self, name: str) -> type | None:
        """Python-тип колонки для приведения значений фильтров."""
        return _PYTHON_TYPES.get(self.column(name).type.split("(")[0])


_PYTHON_TYPES: dict[str, type] = {
    "UUID": __import__("uuid").UUID,
    "INTEGER": int,
    "NUMERIC": __import__("decimal").Decimal,
    "BOOLEAN": bool,
    "DATETIME": __import__("datetime").datetime,
    "DATE": __import__("datetime").date,
    "TIMESTAMP": __import__("datetime").datetime,
    "TIMESTAMPTZ": __import__("datetime").datetime,
}


def _is_masked(column_name: str) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(column_name, pattern) for pattern in MASKED_PATTERNS)


def _module_by_schema() -> dict[str, str]:
    from src.core.plugins import MANIFESTS

    return {m.db_schema: m.name for m in MANIFESTS}


def build_cache() -> dict[str, TableMeta]:
    """Собрать кэш из Base.metadata (вызывается при старте и rebuild)."""
    modules = _module_by_schema()
    cache: dict[str, TableMeta] = {}
    for table in Base.metadata.sorted_tables:
        schema = table.schema or "public"
        # ORM-описанные таблицы только; системных каталогов тут нет by design
        columns = []
        for column in table.columns:
            fk = None
            for foreign in column.foreign_keys:
                fk = f"{foreign.column.table.schema}.{foreign.column.table.name}.{foreign.column.name}"
                break
            columns.append(ColumnMeta(
                name=column.name,
                type=str(column.type),
                nullable=column.nullable,
                pk=column.primary_key,
                fk=fk))
        company_scoped = any(
            c.name == "company_id" and (c.fk or "").startswith("erp_core.companies")
            for c in columns)
        title = (table.comment or "").strip().splitlines()[0] if table.comment else table.name
        # docstring моделей SQLAlchemy кладёт в comment (comment=True)
        if title == table.name:
            for mapper in Base.registry.mappers:
                if mapper.local_table is table and mapper.class_.__doc__:
                    title = mapper.class_.__doc__.strip().splitlines()[0]
                    break
        masked = [c.name for c in columns if _is_masked(c.name)]
        cache[f"{schema}.{table.name}"] = TableMeta(
            schema=schema,
            table=table.name,
            module=modules.get(schema, schema),
            title=title or table.name,
            company_scoped=company_scoped,
            platform_only=f"{schema}.{table.name}" in PLATFORM_ONLY,
            columns=columns,
            masked=masked)
    return cache


_cache: dict[str, TableMeta] | None = None
_lock = threading.Lock()


def tables_cache() -> dict[str, TableMeta]:
    """Кэш метаданных (потокобезопасная ленивая инициализация)."""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = build_cache()
    return _cache


def rebuild_cache() -> int:
    """Перестройка (платформенный контекст). Возвращает размер кэша."""
    global _cache
    with _lock:
        _cache = build_cache()
    return len(_cache)


def get_table(schema: str, table: str) -> TableMeta:
    """Таблица из кэша; неизвестная → 422 (инъекции §9.3)."""
    meta = tables_cache().get(f"{schema}.{table}")
    if meta is None:
        raise TableBrowserError(
            "unknown_table", f"нет таблицы {schema}.{table} в реестре")
    return meta
