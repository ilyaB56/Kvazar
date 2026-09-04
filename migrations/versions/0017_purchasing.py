"""purchasing (resources-core, этап B): заказы поставщику и приёмки

Таблицы purchase_orders/purchase_order_lines/receipts/receipt_lines
(схема mgmt_accounting) + виды документов ЗП (заказ поставщику) и
ПМ (приёмка товара) для нумерации по doc_sequences.

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(40), unique=True),
        sa.Column("counterparty_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.counterparties.id"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8)),  # заморожен при создании (ADR-003/§2.6)
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount_base", sa.Numeric(20, 4), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'partially_received', "
            "'received', 'closed', 'cancelled')",
            name="ck_purchase_orders_status",
        ),
        schema=S,
    )
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.purchase_orders.id"), nullable=False, index=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("qty", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_po_lines_qty_positive"),
        schema=S,
    )
    op.create_table(
        "receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(40), unique=True),
        sa.Column("purchase_order_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.purchase_orders.id"), index=True),
        sa.Column("counterparty_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.counterparties.id"), nullable=False, index=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("is_stornoed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("counterparty_doc", sa.String(60)),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("moved_at", sa.Date(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('draft', 'posted')", name="ck_receipts_status"),
        schema=S,
    )
    op.create_table(
        "receipt_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("receipt_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.receipts.id"), nullable=False, index=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.locations.id")),
        sa.Column("qty", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 4)),  # базовая валюта
        sa.Column("serial_codes", JSONB()),
        sa.CheckConstraint("qty > 0", name="ck_receipt_lines_qty_positive"),
        schema=S,
    )
    op.create_index("ix_receipt_lines_item", "receipt_lines", ["item_id"], schema=S)
    op.execute(
        f"INSERT INTO {S}.doc_types (code, title, number_prefix) VALUES "
        "('ЗП', 'Заказ поставщику', 'ЗП'), "
        "('ПМ', 'Приёмка товара', 'ПМ')"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {S}.doc_types WHERE code IN ('ЗП', 'ПМ')")
    op.drop_table("receipt_lines", schema=S)
    op.drop_table("receipts", schema=S)
    op.drop_table("purchase_order_lines", schema=S)
    op.drop_table("purchase_orders", schema=S)
