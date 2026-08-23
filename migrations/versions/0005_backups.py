"""backups: реестр резервных копий (updates-and-backups-spec, этап A)

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"


def upgrade() -> None:
    op.create_table(
        "backups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),  # manual|scheduled|pre_update
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=CORE,
    )


def downgrade() -> None:
    op.drop_table("backups", schema=CORE)
