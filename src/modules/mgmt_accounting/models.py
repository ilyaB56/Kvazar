"""Модели модуля управленческого учёта — схема mgmt_accounting.

Деньги — NUMERIC(20,4), курсы — NUMERIC(18,8), никаких float (ADR-003).
Остатки счетов не хранятся — считаются по транзакциям.
"""

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

SCHEMA = "mgmt_accounting"


class Account(Base):
    """Счёт/кошелёк организации (multitenancy §5.3: NOT NULL с этапа B)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))  # ISO 4217
    # «РасчСчет» выгрузки клиент-банк 1С (showcase-chain, этап E)
    account_number: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    """Статья доходов/расходов/переводов; иерархия через parent_id одним списком."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.categories.id"))
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))  # income | expense | transfer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Counterparty(Base):
    """Контрагент: внутренний автономер + реквизиты; ИНН+КПП — ключ поиска дублей."""

    __table_args__ = (
        UniqueConstraint("company_id", "internal_code"),
        {"schema": SCHEMA},
    )
    __tablename__ = "counterparties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    internal_code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12), default="")
    kpp: Mapped[str] = mapped_column(String(9), default="")
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.contacts.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DocType(Base):
    """Вид документа — данные, не код: новые виды добавляются строкой справочника."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "doc_types"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    number_prefix: Mapped[str] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DocSequence(Base):
    """Счётчик номеров вида документа в году; захват — SELECT ... FOR UPDATE."""

    __table_args__ = (
        UniqueConstraint("company_id", "doc_type_code", "year"),
        {"schema": SCHEMA},
    )
    __tablename__ = "doc_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    doc_type_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.doc_types.code"))
    year: Mapped[int] = mapped_column(Integer)
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class Rate(Base):
    """Курс валюты к базовой на дату (базовая валюта курса не требует)."""

    __table_args__ = (
        UniqueConstraint("date", "currency"),
        {"schema": SCHEMA},
    )
    __tablename__ = "rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual | cbr | connector


class Period(Base):
    """Месячный период; создаётся лениво при первой операции в нём."""

    __table_args__ = (
        UniqueConstraint("company_id", "year", "month"),
        {"schema": SCHEMA},
    )
    __tablename__ = "periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Transaction(Base):
    """Документ/транзакция. Суммы положительные; направление задаёт kind.

    rate/amount_base замораживаются при проведении (ADR-003). Для transfer
    основная сторона — списание (account_id), парная — зачисление (account_to_id).
    """

    __table_args__ = (
        UniqueConstraint("company_id", "doc_number"),
        {"schema": SCHEMA},
    )
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.companies.id"), index=True, nullable=False
    )
    doc_number: Mapped[str | None] = mapped_column(String(40))  # присваивается при проведении
    doc_type_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.doc_types.code"))
    kind: Mapped[str] = mapped_column(String(10))  # income | expense | transfer
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | posted

    operated_at: Mapped[date] = mapped_column(Date, index=True)  # дата операции (пользовательская)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    amount_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))

    # сторона зачисления (только transfer)
    amount_to: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency_to: Mapped[str | None] = mapped_column(String(3))
    rate_to: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    amount_to_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.accounts.id"), index=True
    )  # для transfer — счёт списания
    account_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.accounts.id"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.categories.id"))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.counterparties.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict)  # гибкие разрезы на будущее

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # метка удаления (только дубли)
    is_stornoed: Mapped[bool] = mapped_column(Boolean, default=False)
    storno_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.transactions.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
