"""mgmt_accounting: схема учёта + erp_core.record_versions

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"
CORE = "erp_core"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")

    # Generic журнал версий ядра: пишется сервисными слоями модулей (core.versioning)
    op.create_table(
        "record_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(100), nullable=False, index=True),
        sa.Column("entity_id", sa.String(64), nullable=False, index=True),
        sa.Column("changed_by", UUID(as_uuid=True)),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("diff", JSONB(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text()),
        schema=CORE,
    )

    op.create_table(
        "doc_types",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("number_prefix", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.create_table(
        "doc_sequences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("doc_type_code", sa.String(10), sa.ForeignKey(f"{S}.doc_types.code"),
                  nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("doc_type_code", "year"),
        schema=S,
    )
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # company_id nullable: v1 — одноконтурная система, компании появляются позже
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.companies.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.categories.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.create_table(
        "counterparties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("internal_code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("inn", sa.String(12), nullable=False, server_default=""),
        sa.Column("kpp", sa.String(9), nullable=False, server_default=""),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.contacts.id")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.create_table(
        "rates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.UniqueConstraint("date", "currency"),
        schema=S,
    )
    op.create_table(
        "periods",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("closed_by", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.users.id")),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("year", "month"),
        schema=S,
    )
    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_number", sa.String(40), unique=True),
        sa.Column("doc_type_code", sa.String(10), sa.ForeignKey(f"{S}.doc_types.code"),
                  nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("operated_at", sa.Date(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8)),
        sa.Column("amount_base", sa.Numeric(20, 4)),
        sa.Column("amount_to", sa.Numeric(20, 4)),
        sa.Column("currency_to", sa.String(3)),
        sa.Column("rate_to", sa.Numeric(18, 8)),
        sa.Column("amount_to_base", sa.Numeric(20, 4)),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.accounts.id"),
                  nullable=False, index=True),
        sa.Column("account_to_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.accounts.id")),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.categories.id")),
        sa.Column("counterparty_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.counterparties.id")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("dimensions", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_stornoed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("storno_of_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.transactions.id")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.users.id"),
                  nullable=False),
        schema=S,
    )

    # нумератор внутренних кодов контрагентов
    op.execute(f"CREATE SEQUENCE {S}.counterparty_code_seq START 1")

    # Сид: виды документов (СТ — сторно) и рублёвый счёт «Касса»
    op.execute(
        f"INSERT INTO {S}.doc_types (code, title, number_prefix) VALUES "
        "('ПК', 'Поступление', 'ПК'), "
        "('СК', 'Списание', 'СК'), "
        "('ПР', 'Перевод', 'ПР'), "
        "('СТ', 'Сторно', 'СТ')"
    )
    op.execute(
        f"INSERT INTO {S}.accounts (id, name, currency) "
        "VALUES (gen_random_uuid(), 'Касса', 'RUB')"
    )


def downgrade() -> None:
    for table in ("transactions", "periods", "rates", "counterparties", "categories",
                  "accounts", "doc_sequences", "doc_types"):
        op.drop_table(table, schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
    op.drop_table("record_versions", schema=CORE)
