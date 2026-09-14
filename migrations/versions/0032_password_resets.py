"""Мультитенантность D: дедлайн 2FA + восстановление пароля (§7.5, этап C-ревью)

Revision ID: 0032
Revises: 0031

- users.totp_setup_deadline (date): руководитель без 2FA блокируется до
  настройки, когда deadline прошёл (= created_at + 7 дней; backfill);
  NULL — дедлайна нет (сотрудники, существующие до этапа D — 7 дней от
  миграции, чтобы не блокировать рабочий вход немедленно)
- erp_core.password_resets: id, user_id FK, token_hash sha256 UNIQUE
  (токен в БД не хранится), expires_at (created + 1 час), used_at NULL
  (одноразовость), created_ip (аудит), created_at
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_setup_deadline", sa.DateTime(timezone=True), nullable=True),
        schema=S)
    # дедлайн только руководителям (admin/pl): created_at + 7 дней;
    # существующим — от миграции, чтобы не блокировать вход сразу
    op.execute(f"""
        UPDATE {S}.users
        SET totp_setup_deadline = (CURRENT_DATE + INTERVAL '7 days')
        WHERE (is_platform_admin OR role = 'admin')
          AND totp_setup_deadline IS NULL
    """)

    op.create_table(
        "password_resets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # токен под Fernet (SECRETS_KEY): открытого текста нет, но обработчик
        # письма восстанавливает его для ссылки (хэш необратим)
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_ip", sa.String(64), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("password_resets", schema=S)
    op.drop_column("users", "totp_setup_deadline", schema=S)
