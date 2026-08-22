"""security-p0: users.token_version + erp_core.revoked_tokens

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"


def upgrade() -> None:
    # версия токенов: смена пароля инвалидирует все ранее выданные токены
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        schema=CORE,
    )
    # blacklist отозванных refresh-токенов (по jti) до их exp
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey(f"{CORE}.users.id"),
                  nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        schema=CORE,
    )


def downgrade() -> None:
    op.drop_table("revoked_tokens", schema=CORE)
    op.drop_column("users", "token_version", schema=CORE)
