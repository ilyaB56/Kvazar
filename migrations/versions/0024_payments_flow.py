"""Онлайн-оплата B: online_payments + item_mappings + flow_runs (sales-automation §4.3–4.5)

Revision ID: 0024
Revises: 0023

online_payments — нормализованный платёж (UNIQUE connection+payment_id,
UUID-ссылки на учёт без FK — паттерн crm_deal_id); item_mappings —
сайт-товар → номенклатура; flow_runs — шаговый журнал сценария
(UNIQUE payment_id, продолжение с последнего шага).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    op.create_table(
        "online_payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_payment_id", sa.String(100), nullable=False),
        # received | processed | manual | ignored
        sa.Column("status", sa.String(12), nullable=False, default="received"),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, default="RUB"),
        sa.Column("buyer", JSONB, nullable=False, default=dict),  # ПДн: маскировать для ro
        sa.Column("lines", JSONB, nullable=False, default=list),
        sa.Column("metadata", JSONB, nullable=False, default=dict),
        sa.Column("webhook_event_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.webhook_events.id", ondelete="SET NULL"), nullable=True),
        # UUID-ссылки на схему учёта — без FK (границы модулей)
        sa.Column("sales_order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("transaction_id", UUID(as_uuid=True), nullable=True),
        sa.Column("shipment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error_step", sa.String(20), nullable=False, default=""),
        sa.Column("error_reason", sa.String(40), nullable=False, default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("connection_id", "provider_payment_id",
                            name="uq_online_payments_conn_payment"),
        schema=S,
    )

    op.create_table(
        "item_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # NULL = глобальный маппинг по sku
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("external_item_id", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(100), nullable=True),
        sa.Column("item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "external_item_id",
                            name="uq_item_mappings_conn_external"),
        schema=S,
    )

    op.create_table(
        "flow_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.online_payments.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("recipe_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.recipes.id", ondelete="SET NULL"), nullable=True),
        # running | done | failed | manual
        sa.Column("status", sa.String(10), nullable=False, default="running"),
        # последний завершённый шаг
        # counterparty | order | confirm | pay | ship | deliver | notify
        sa.Column("step", sa.String(14), nullable=False, default=""),
        sa.Column("context", JSONB, nullable=False, default=dict),
        sa.Column("error", sa.Text, nullable=False, default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, default=1),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("flow_runs", schema=S)
    op.drop_table("item_mappings", schema=S)
    op.drop_table("online_payments", schema=S)
