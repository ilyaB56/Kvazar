"""Devtools этап B (devtools-spec §7.1, §10): ракурсы ведения

Revision ID: 0038
Revises: 0037

- erp_core.maintenance_views: именованные ракурсы; уникальность имени
  (company_id, name) NULLS NOT DISTINCT (PG16). Downgrade: drop table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"


def upgrade() -> None:
    op.create_table(
        "maintenance_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.companies.id", ondelete="CASCADE"),
                  nullable=True, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("table_schema", sa.String(63), nullable=False),
        sa.Column("table_name", sa.String(63), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),  # domain|direct
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        schema=S,
    )
    op.execute(
        f"ALTER TABLE {S}.maintenance_views"
        " ADD CONSTRAINT uq_maintenance_views_company_id_name"
        " UNIQUE NULLS NOT DISTINCT (company_id, name)")


def downgrade() -> None:
    op.drop_table("maintenance_views", schema=S)
