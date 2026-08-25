"""api_tokens: служебные API-токены (showcase-chain, этап A)

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="user"),
        # действия токена записываются от имени владельца (created_by — FK users)
        sa.Column("owner_user_id", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.users.id")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        schema=CORE,
    )


def downgrade() -> None:
    op.drop_table("api_tokens", schema=CORE)
