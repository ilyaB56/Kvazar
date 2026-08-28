"""ai_agent: chat_sessions + chat_messages (этап C — полное логирование ADR-006 п.7)

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "ai_agent"


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False, index=True),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.chat_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),  # user|assistant|system
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", JSONB(), nullable=False, server_default="{}"),  # sources, tools_used
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("chat_messages", schema=S)
    op.drop_table("chat_sessions", schema=S)
