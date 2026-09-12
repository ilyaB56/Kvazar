"""Контроль сеансов: erp_core.auth_sessions (sessions-security-spec §2.1)

Revision ID: 0026
Revises: 0025

Сущность «активный сеанс входа»: id = sid токенов, пользователь,
user_agent/ip входа, created/last_used/revoked. Активный = revoked_at
IS NULL и TTL refresh-токена не истёк.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id"), nullable=False, index=True),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, index=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("auth_sessions", schema=S)
