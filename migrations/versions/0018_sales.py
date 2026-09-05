"""sales (resources-core, этап C): заказы клиентов и отгрузки

Таблицы sales_orders/sales_order_lines/shipments/shipment_lines
(схема mgmt_accounting) + виды документов ЗК (заказ клиента) и
ОТ (отгрузка) для doc_sequences.

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.create_table(
        "sales_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(40), unique=True),
        sa.Column("counterparty_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.counterparties.id"), nullable=False, index=True),
        # сделка mini_crm — по UUID без FK (модули не трогают чужие схемы)
        sa.Column("crm_deal_id", UUID(as_uuid=True), index=True),
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
            "status IN ('draft', 'confirmed', 'partially_shipped', "
            "'shipped', 'closed', 'cancelled')",
            name="ck_sales_orders_status",
        ),
        schema=S,
    )
    op.create_table(
        "sales_order_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.sales_orders.id"), nullable=False, index=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("qty", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        # резерв v1 — статус строки (§3.4); жёсткая блокировка — v2
        sa.Column("reserved_qty", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.CheckConstraint("qty > 0", name="ck_so_lines_qty_positive"),
        schema=S,
    )
    op.create_table(
        "shipments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(40), unique=True),
        sa.Column("sales_order_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.sales_orders.id"), nullable=False, index=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("is_stornoed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("counterparty_doc", sa.String(60)),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("moved_at", sa.Date(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('draft', 'posted')", name="ck_shipments_status"),
        schema=S,
    )
    op.create_table(
        "shipment_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("shipment_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.shipments.id"), nullable=False, index=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.locations.id")),
        sa.Column("qty", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 4)),  # валюта заказа (префилл из строки)
        sa.Column("amount_base", sa.Numeric(20, 4)),  # выручка, при проведении
        sa.Column("serial_codes", JSONB()),  # пусто → FIFO-автовыбор при post
        sa.CheckConstraint("qty > 0", name="ck_shipment_lines_qty_positive"),
        schema=S,
    )
    op.execute(
        f"INSERT INTO {S}.doc_types (code, title, number_prefix) VALUES "
        "('ЗК', 'Заказ клиента', 'ЗК'), "
        "('ОТ', 'Отгрузка', 'ОТ')"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {S}.doc_types WHERE code IN ('ЗК', 'ОТ')")
    op.drop_table("shipment_lines", schema=S)
    op.drop_table("shipments", schema=S)
    op.drop_table("sales_order_lines", schema=S)
    op.drop_table("sales_orders", schema=S)
