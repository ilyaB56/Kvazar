"""Делегируемые роли: user_permissions + username + дедлайн пароля (role-delegation §5, §12)

Revision ID: 0033
Revises: 0032

- erp_core.user_permissions: PK (user_id, module), level rw|ro (нет
  строки = none — симметрично role_permissions), granted_by FK users
  (право отзыва), granted_at; индекс granted_by (каскад). Пустая
  таблица = поведение идентично текущему.
- users.username (UNIQUE, NULL у существующих): логин из ФИО при
  создании учётки из настроек (§12); вход — username ИЛИ email.
- users.must_change_password_by (ts NULL): дедлайн смены временного
  пароля (+72ч при первой выдаче прав); после дедлайна мутации 403
  password_expired, чтение доступно.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "user_permissions",
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("module", sa.String(30), primary_key=True),
        sa.Column("level", sa.String(5), nullable=False),  # rw | ro
        sa.Column("granted_by", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id"), nullable=False, index=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )
    op.add_column("users",
                  sa.Column("username", sa.String(64), nullable=True),
                  schema=S)
    op.create_unique_constraint("uq_users_username", "users", ["username"],
                                schema=S)
    op.add_column("users",
                  sa.Column("must_change_password_by",
                            sa.DateTime(timezone=True), nullable=True),
                  schema=S)


def downgrade() -> None:
    op.drop_column("users", "must_change_password_by", schema=S)
    op.drop_constraint("uq_users_username", "users", schema=S)
    op.drop_column("users", "username", schema=S)
    op.drop_table("user_permissions", schema=S)
