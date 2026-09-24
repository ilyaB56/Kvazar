"""Ozon Seller: кэш-каталог и журналы (ozon-connector-spec §4, этап A)

Revision ID: 0035
Revises: 0034

- integrations.ozon_products: кэш товаров (UNIQUE connection_id+offer_id)
- integrations.ozon_stocks: снапшот остатков Ozon (чужой склад, без movements)
- integrations.ozon_orders: заказы-постинги (UNIQUE connection_id+posting_number)
- integrations.ozon_transactions: комиссии (UNIQUE connection_id+operation_id)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    op.create_table(
        "ozon_products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey("erp_core.companies.id"), nullable=True, index=True),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id"), nullable=False, index=True),
        sa.Column("offer_id", sa.String(200), nullable=False),
        sa.Column("product_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("sku", sa.String(100), nullable=True),
        sa.Column("name", sa.String(500), nullable=False, server_default=""),
        sa.Column("price", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "offer_id", name="uq_ozon_products_conn_offer"),
        schema=S,
    )
    op.create_table(
        "ozon_stocks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey("erp_core.companies.id"), nullable=True, index=True),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id"), nullable=False, index=True),
        sa.Column("offer_id", sa.String(200), nullable=False),
        sa.Column("warehouse_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "ozon_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey("erp_core.companies.id"), nullable=True, index=True),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id"), nullable=False, index=True),
        sa.Column("posting_number", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("order_date", sa.String(30), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("lines", JSONB(), nullable=False, server_default="[]"),
        sa.Column("sales_order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("mapping_error", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("connection_id", "posting_number", name="uq_ozon_orders_conn_posting"),
        schema=S,
    )
    op.create_table(
        "ozon_transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey("erp_core.companies.id"), nullable=True, index=True),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id"), nullable=False, index=True),
        sa.Column("operation_id", sa.String(100), nullable=False),
        sa.Column("operation_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("items", JSONB(), nullable=False, server_default="[]"),
        sa.Column("posted_at", sa.String(30), nullable=False, server_default=""),
        sa.Column("transaction_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "operation_id", name="uq_ozon_txns_conn_op"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("ozon_transactions", schema=S)
    op.drop_table("ozon_orders", schema=S)
    op.drop_table("ozon_stocks", schema=S)
    op.drop_table("ozon_products", schema=S)
