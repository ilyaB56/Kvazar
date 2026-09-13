"""Модели фичи sales (resources-core §3.4): заказы клиентов и отгрузки.

Схема mgmt_accounting (ADR-007). Зеркально закупкам: заказ — валюта +
замороженный курс при создании, номер при confirm; отгрузка — проведение
создаёт движения «склад → Клиент», списание по средней, выдачу серийников
(FIFO или явным списком). crm_deal_id — ссылка на сделку mini_crm по UUID
без FK (модули не трогают чужие схемы, как с контрагентом в CRM).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import CORE_SCHEMA
from src.db import Base

SCHEMA = "mgmt_accounting"

ORDER_STATUSES = ("draft", "confirmed", "partially_shipped", "shipped", "closed", "cancelled")
ORDER_DOC_TYPE = "ЗК"  # заказ клиента (doc_types, seed 0018)
SHIPMENT_DOC_TYPE = "ОТ"  # отгрузка


class SalesOrder(Base):
    """Заказ клиента: строки qty × unit_price в валюте заказа, курс заморожен
    при создании; номер ЗК-… выделяется при confirm; crm_deal_id — связь
    со сделкой (префилл контрагента, кнопка «Создать заказ» — UI этап I)."""

    __table_args__ = (
        UniqueConstraint("company_id", "number"),
        {"schema": SCHEMA},
    )
    __tablename__ = "sales_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    number: Mapped[str | None] = mapped_column(String(40))
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.counterparties.id"), index=True
    )
    crm_deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))  # заморожен при создании (§2.6)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # в валюте заказа
    amount_base: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalesOrderLine(Base):
    """Строка заказа; reserved_qty — резерв v1 как статус строки (§3.4):
    подтверждённый заказ резервирует qty, отгрузка уменьшает; жёсткая
    блокировка и резерв по сроку — v2."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sales_orders.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # валюта заказа
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # qty × unit_price
    reserved_qty: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), default=0, server_default="0"
    )


class Shipment(Base):
    """Отгрузка: проведение = движения «склад → Клиент» + серийники sold +
    списание по средней + статус заказа; unpost — сторно парными
    инверсионными движениями «Клиент → склад»."""

    __table_args__ = (
        UniqueConstraint("company_id", "number"),
        {"schema": SCHEMA},
    )
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    number: Mapped[str | None] = mapped_column(String(40))  # при post
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sales_orders.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | posted
    is_stornoed: Mapped[bool] = mapped_column(Boolean, default=False)
    counterparty_doc: Mapped[str | None] = mapped_column(String(60))  # номер ТН/акта
    note: Mapped[str] = mapped_column(Text, default="")
    moved_at: Mapped[date] = mapped_column(Date)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # разовая выдача кодов (sales-automation §5.2): повторный deliver — 409
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_via: Mapped[str] = mapped_column(String(12), default="")  # email|telegram|both|manual


class ShipmentLine(Base):
    """Строка отгрузки: serial_ids — ссылки на выдаваемые item_serials
    (явный выбор при создании или FIFO-автозаполнение при проведении);
    открытые коды в БД не хранятся — расшифровка только rw в ответах API.
    unit_price (валюта заказа) и amount_base (выручка) заполняются из
    строки заказа при создании/проведении."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "shipment_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.shipments.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.locations.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # валюта заказа
    amount_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # выручка при post
    # ссылки на item_serials (UUID) — коды-активы не хранятся открыто;
    # serial_codes (legacy) после миграции 0022 не заполняется
    serial_ids: Mapped[list | None] = mapped_column(JSONB)
    serial_codes: Mapped[list | None] = mapped_column(JSONB)
