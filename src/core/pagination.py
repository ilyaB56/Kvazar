"""Пагинация списков (полировка производительности, 2026-09-15).

Совместимость: если запрос НЕ передаёт limit (и offset/format) — эндпоинт
возвращает полный список массивом, как раньше (существующие тесты/смоки
не меняются). При любом из параметров limit/offset/format=paginated —
словарь {"items": [...], "total": N}.

- limit=50&offset=0 — страница; limit=0 — все строки (кнопка «Загрузить
  все»), total всё равно считается
- total — для «Показано 50 из 1234»; считаем по subquery (без выборки)
"""

from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Конверт пагинированного ответа: response_model=list[X] | Page[X]."""

    items: list[T]
    total: int


class PageParams:
    """Зависимость: limit/offset. limit is None → старый непагинированный
    формат (массив) — совместимость; limit передан → {items, total}.

    transform — опциональная пост-обработка строки (сборка Out-схемы),
    применяется ДО оборачивания в конверт, чтобы на выходе был список
    готовых объектов, а не ORM-строк."""

    def __init__(self, limit: int | None = None, offset: int = 0,
                 fmt: str | None = None):
        self.limit = limit
        self.offset = offset
        self.paginated = limit is not None or fmt == "paginated"

    def apply(self, db: Session, query: Select,
              transform: Callable[[Any], Any] | None = None) -> Any:
        if not self.paginated:
            rows = db.scalars(query).all()
            return [transform(r) for r in rows] if transform else rows
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        paged = query
        if self.limit and self.limit > 0:
            paged = paged.limit(self.limit)
        if self.offset:
            paged = paged.offset(self.offset)
        rows = db.scalars(paged).all()
        if transform:
            rows = [transform(r) for r in rows]
        return {"items": rows, "total": int(total)}


def page_params(
    limit: int | None = Query(default=None, ge=0, le=100_000,
                              description="Страница: 50 по умолчанию у клиента; 0 — все"),
    offset: int = Query(default=0, ge=0),
    fmt: str | None = Query(default=None, pattern="^paginated$", alias="format"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset, fmt=fmt)
