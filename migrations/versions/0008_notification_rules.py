"""notification_rules: Telegram-уведомления по событиям (showcase-chain, этап D)

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    op.create_table(
        "notification_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("event_name", sa.String(100), nullable=False, index=True),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("template", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("notification_rules", schema=S)
