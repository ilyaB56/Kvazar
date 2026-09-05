"""Модели фичи production (resources-core §3.5): тех.карты и заказы на сборку.

Схема mgmt_accounting (ADR-007). Тех.карта — одноуровневый BOM: продукция
(qty_out за одно применение) + компоненты с количествами (jsonb). Заказ:
qty_planned применений карты; проведение — списание компонентов
«Склад → Производство» по средней и оприходование продукции
«Производство → Склад» по себестоимости материалов, одной транзакцией.
Частичное и трудозатраты — v2 (§9).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import CORE_SCHEMA
from src.db import Base

SCHEMA = "mgmt_accounting"

ORDER_STATUSES = ("draft", "posted", "cancelled")
ORDER_DOC_TYPE = "СБ"  # сборка (doc_types, seed 0019)


class TechCard(Base):
    """Спецификация: продукция + компоненты, одноуровневая (§3.5)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "tech_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    product_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.items.id"), index=True
    )
    qty_out: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # продукции за применение
    components: Mapped[list] = mapped_column(JSONB)  # [{item_id, qty}]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionOrder(Base):
    """Заказ на сборку: qty_planned применений карты; номер СБ-… при
    проведении; material_cost — себестоимость материалов всей партии."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "production_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str | None] = mapped_column(String(40), unique=True)
    tech_card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.tech_cards.id"), index=True
    )
    qty_planned: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # применений карты
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|posted|cancelled
    is_stornoed: Mapped[bool] = mapped_column(Boolean, default=False)
    material_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # при post
    moved_at: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
