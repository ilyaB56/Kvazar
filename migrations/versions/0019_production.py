"""production (resources-core, этап D): тех.карты и заказы на сборку

Таблицы tech_cards/production_orders (схема mgmt_accounting) + вид
документа СБ (сборка) для doc_sequences.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.create_table(
        "tech_cards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("product_item_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.items.id"), nullable=False, index=True),
        sa.Column("qty_out", sa.Numeric(20, 4), nullable=False),
        # одноуровневый BOM: [{item_id, qty}]
        sa.Column("components", JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty_out > 0", name="ck_tech_cards_qty_out_positive"),
        schema=S,
    )
    op.create_table(
        "production_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(40), unique=True),
        sa.Column("tech_card_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.tech_cards.id"), nullable=False, index=True),
        sa.Column("qty_planned", sa.Numeric(20, 4), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("is_stornoed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("material_cost", sa.Numeric(20, 4)),  # себестоимость материалов, при post
        sa.Column("moved_at", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('draft', 'posted', 'cancelled')",
                           name="ck_production_orders_status"),
        sa.CheckConstraint("qty_planned > 0", name="ck_production_orders_qty_positive"),
        schema=S,
    )
    op.execute(
        f"INSERT INTO {S}.doc_types (code, title, number_prefix) VALUES "
        "('СБ', 'Сборка', 'СБ')"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {S}.doc_types WHERE code = 'СБ'")
    op.drop_table("production_orders", schema=S)
    op.drop_table("tech_cards", schema=S)
