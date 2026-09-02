"""mini_crm: communications + activities (этап B)

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mini_crm"


def upgrade() -> None:
    op.create_table(
        "communications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.deals.id"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),  # call|email|meeting|note|other
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Date(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "activities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.deals.id"),
                  nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("due_at", sa.Date(), nullable=False, index=True),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("activities", schema=S)
    op.drop_table("communications", schema=S)
