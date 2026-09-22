"""Самообслуживание клиентов (блок 2): заявки на регистрацию

Revision ID: 0034
Revises: 0033

- erp_core.signup_requests: публичная заявка (name, company_name, email),
  пароль сразу хэшем (задаётся при регистрации, активируется с организацией
  при одобрении), verify-токен sha256 UNIQUE (urlsafe-32, в БД открытого
  текста нет; token_enc Fernet — для письма), 24 часа, одноразовость;
  status: pending → verified → approved/rejected; org_id — созданная
  организация (после одобрения); created_ip — аудит
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "signup_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False,
                  server_default=""),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        # пароль регистрации: hash сразу; live-вход — только после одобрения
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        # pending / verified / approved / rejected
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="pending"),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.companies.id"), nullable=True),
        sa.Column("created_ip", sa.String(64), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("signup_requests", schema=S)
