"""Модели mini_crm — схема mini_crm (этапы B/C дополняют)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import CORE_SCHEMA
from src.db import Base

SCHEMA = "mini_crm"


class Stage(Base):
    """Стадия воронки: вероятность 0–100, флаги won/lost (конечные)."""

    __table_args__ = (
        UniqueConstraint("position"),
        {"schema": SCHEMA},
    )
    __tablename__ = "stages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer)
    probability: Mapped[int | None] = mapped_column(Integer)  # 0–100, nullable у won/lost
    is_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_lost: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Deal(Base):
    """Сделка. Контрагент учёта — по UUID без FK (модули не трогают чужие схемы);
    имя тянется публичным API с кэшем на запрос. rate/amount_base замораживаются
    при создании/правке amount|currency (ADR-003)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.stages.id"), index=True)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.contacts.id"))
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"), index=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    amount_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))

    expected_close_at: Mapped[date | None] = mapped_column(Date)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())


class Communication(Base):
    """Коммуникация по сделке: звонок/письмо/встреча/заметка."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "communications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.deals.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # call|email|meeting|note|other
    content: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[date] = mapped_column(Date)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(Base):
    """Задача по сделке; просроченные — фильтром (due_before + status=open)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.deals.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    due_at: Mapped[date] = mapped_column(Date, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
