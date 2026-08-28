"""ai_agent: proposals + user_settings (этап E — запись через подтверждение, ADR-006 п.5)

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "ai_agent"


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False, index=True),
        sa.Column("action_type", sa.String(40), nullable=False),  # create_transaction | categorize
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, index=True,
                  server_default="pending"),  # pending|approved|rejected|auto_applied|failed
        # идемпотентность этапа F: хэш item+job
        sa.Column("idempotency_key", sa.String(64), nullable=True, index=True),
        sa.Column("result", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id")),
        schema=S,
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  primary_key=True),
        sa.Column("autopapply", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("user_settings", schema=S)
    op.drop_table("proposals", schema=S)
