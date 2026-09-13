"""Модели фичи inventory (ADR-007: фичи-подпакеты внутри mgmt_accounting).

Схема общая — mgmt_accounting. Остатки не хранятся: представление
v_stock_balances поверх движений (двойная запись, §2.2 resources-core).
Деньги/количества — NUMERIC(20,4), никаких float (ADR-003).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import CORE_SCHEMA
from src.db import Base

SCHEMA = "mgmt_accounting"

ITEM_KINDS = ("physical", "digital", "service")
TRACKING_MODES = ("qty", "serial")
SERIAL_STATUSES = ("in_stock", "reserved", "sold", "void")

# source_type движения: этап A — ручные операции; документы (закупки/
# отгрузки/сборка) добавляют свои типы на этапах B–D
MOVE_SOURCES = ("stock_transfer", "stock_adjustment")


class Unit(Base):
    """Справочник единиц измерения (seed миграции; шт, кг, л, м, час, мес, лицензия)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "units"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Item(Base):
    """Номенклатура: физический/цифровой товар или услуга.

    Услуга движений не имеет вообще (только строки заказов и деньги, §3.1).
    avg_cost — средняя взвешенная себестоимость в базовой валюте (§4);
    NULL = приходов не было. low_stock_threshold — порог события
    acc.inventory.low_stock (NULL = не проверять).
    """

    __table_args__ = (
        UniqueConstraint("company_id", "sku"),
        {"schema": SCHEMA},
    )
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))  # physical | digital | service
    unit_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.units.code"))
    tracking: Mapped[str] = mapped_column(String(10), default="qty")  # qty | serial
    barcode: Mapped[str | None] = mapped_column(String(64))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # базовая валюта
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    low_stock_threshold: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Location(Base):
    """Склад (physical/digital) или системная транзитная локация.

    Транзитные (Поставщик, Клиент, Производство, Брак) создаёт миграция,
    редактирования через API нет — только GET/POST пользовательских.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "name"),
        {"schema": SCHEMA},
    )
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))  # physical | digital
    is_transit: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockMove(Base):
    """Движение товара двойной записью: одна строка from → to (§2.2).

    Остаток локации = Σ входов − Σ выходов. Товар возникает только из
    транзита (Поставщик/Брак) и исчезает только в транзит (Клиент/Брак).
    unit_cost — базовая валюта; у расходных записей равен avg_cost на
    момент (§4), новую цену несут только приходы.
    """

    __table_args__ = (
        Index("ix_stock_moves_item_from", "item_id", "from_location_id"),
        Index("ix_stock_moves_item_to", "item_id", "to_location_id"),
        Index("ix_stock_moves_moved_at", "moved_at"),
        {"schema": SCHEMA},
    )
    __tablename__ = "stock_moves"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    from_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.locations.id"))
    to_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.locations.id"))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.counterparties.id")
    )
    source_type: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    moved_at: Mapped[date] = mapped_column(Date)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemSerial(Base):
    """Поэкземплярный учёт цифровых товаров: код = актив, хранится Fernet-шифрованным.

    Fernet не детерминирован, поэтому уникальность и поиск — по sha256-отпечатку
    code_hash; исходный код восстанавливается расшифровкой code_enc.
    """

    __table_args__ = (
        UniqueConstraint("code_hash"),
        {"schema": SCHEMA},
    )
    __tablename__ = "item_serials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"), index=True)
    code_enc: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="in_stock")  # in_stock|reserved|sold|void
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.locations.id"))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    received_move_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.stock_moves.id"))
    sold_move_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.stock_moves.id"))
    received_at: Mapped[date] = mapped_column(Date)
