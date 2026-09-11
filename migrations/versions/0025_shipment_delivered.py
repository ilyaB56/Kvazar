"""Онлайн-оплата C: shipments.delivered_at/delivered_via (sales-automation §5.2)

Revision ID: 0025
Revises: 0024

Разовая выдача кодов: delivered_at фиксирует факт, повторный deliver —
409; delivered_via — канал доставки (email/telegram/both/manual).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.add_column("shipments",
                  sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
                  schema=S)
    op.add_column("shipments",
                  sa.Column("delivered_via", sa.String(12), nullable=False,
                            server_default=""),
                  schema=S)


def downgrade() -> None:
    op.drop_column("shipments", "delivered_via", schema=S)
    op.drop_column("shipments", "delivered_at", schema=S)
