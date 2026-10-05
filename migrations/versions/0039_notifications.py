"""Notifications этап A (notifications-spec §5): erp_core.notifications

Revision ID: 0039
Revises: 0038

- fan-out модель: строка = один адресат (user_id NOT NULL), audience —
  происхождение адресации; company_id NULL = платформенное.
- UNIQUE (user_id, dedup_key) WHERE dedup_key IS NOT NULL — схлопывание
  повторов per-получателя; partial-индексы под список и счётчик.
Downgrade: drop table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.companies.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False, index=True),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("link", sa.String(255), nullable=False, server_default=""),
        sa.Column("dedup_key", sa.String(255), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=S,
    )
    op.create_index(
        "uq_notifications_user_dedup", "notifications",
        ["user_id", "dedup_key"], unique=True, schema=S,
        postgresql_where=sa.text("dedup_key IS NOT NULL"))
    op.create_index(
        "ix_notifications_user_created", "notifications",
        ["user_id", sa.text("created_at DESC")], schema=S)
    op.create_index(
        "ix_notifications_user_unread", "notifications",
        ["user_id"], schema=S, postgresql_where=sa.text("read_at IS NULL"))
    op.create_index(
        "ix_notifications_company_created", "notifications",
        ["company_id", sa.text("created_at DESC")], schema=S)


def downgrade() -> None:
    op.drop_table("notifications", schema=S)
