"""Мультитенантность C: 2FA TOTP (multitenancy-spec §5.4)

Revision ID: 0031
Revises: 0030

- erp_core.user_totp: user_id PK FK users ON DELETE CASCADE, secret_enc
  (Fernet по SECRETS_KEY), confirmed_at NULL (NULL = настроен, но не
  подтверждён), enabled_at — момент включения
- erp_core.totp_backup_codes: id, user_id FK, code_hash sha256 (как
  api_tokens), used_at NULL, created_at. 10 кодов «XXXX-XXXX»,
  показываются один раз, каждый расходуется единожды.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "user_totp",
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "totp_backup_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("totp_backup_codes", schema=S)
    op.drop_table("user_totp", schema=S)
