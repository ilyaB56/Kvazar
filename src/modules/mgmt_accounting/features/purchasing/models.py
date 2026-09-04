"""Модели фичи purchasing (resources-core §3.3): заказы поставщику и приёмки.

Схема mgmt_accounting (ADR-007). Заказ: валюта + замороженный курс на момент
создания (§2.6), сумма в валюте и базовая. Приёмка: черновик → проведение
(движения + серийники + пересчёт средней) → сторно (unpost, парные
инверсионные движения). Деньги — NUMERIC(20,4) в валюте, суммы к оплате —
в базовой; количества — NUMERIC(20,4) (ADR-003).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import CORE_SCHEMA
from src.db import Base

SCHEMA = "mgmt_accounting"

ORDER_STATUSES = ("draft", "confirmed", "partially_received", "received", "closed", "cancelled")
ORDER_DOC_TYPE = "ЗП"  # заказ поставщику (doc_types, seed 0017)
RECEIPT_DOC_TYPE = "ПМ"  # приёмка товара


class PurchaseOrder(Base):
    """Заказ поставщику: строки с ценой в валюте заказа, курс заморожен
    при создании; номер выделяется при confirm (doc_sequences)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str | None] = mapped_column(String(40), unique=True)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.counterparties.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft")
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))  # заморожен при создании (§2.6)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # в валюте заказа
    amount_base: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PurchaseOrderLine(Base):
    """Строка заказа: qty × unit_price в валюте заказа; услуга допустима
    (движений не имеет — только деньги, §3.1)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.purchase_orders.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # валюта заказа
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # qty × unit_price


class Receipt(Base):
    """Приёмка: проведение создаёт движения Поставщик → склад + серийники +
    пересчёт средней (одна транзакция, §2.4); unpost — сторно парными
    инверсионными движениями, только без последующих движений."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str | None] = mapped_column(String(40), unique=True)  # при post
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.purchase_orders.id"), index=True
    )  # nullable — можно без заказа
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.counterparties.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | posted
    is_stornoed: Mapped[bool] = mapped_column(Boolean, default=False)
    counterparty_doc: Mapped[str | None] = mapped_column(String(60))  # накладная поставщика
    note: Mapped[str] = mapped_column(Text, default="")
    moved_at: Mapped[date] = mapped_column(Date)  # дата приёмки
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReceiptLine(Base):
    """Строка приёмки: себестоимость в базовой валюте (для валютной закупки —
    пересчёт по замороженному курсу заказа); serial_codes хранятся в черновике,
    при проведении регистрируются в item_serials (qty = len, §3.3)."""

    __table_args__ = (
        Index("ix_receipt_lines_item", "item_id"),
        {"schema": SCHEMA},
    )
    __tablename__ = "receipt_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.receipts.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.items.id"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.locations.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # базовая валюта
    serial_codes: Mapped[list | None] = mapped_column(JSONB)
