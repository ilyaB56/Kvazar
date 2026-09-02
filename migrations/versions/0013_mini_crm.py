"""mini_crm: схема, стадии воронки (сид), сделки + pg_trgm-поиск (этап A)

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mini_crm"

STAGE_SEED = [
    ("Новая", 10, 10, False, False),
    ("Квалификация", 30, 30, False, False),
    ("Предложение", 50, 50, False, False),
    ("Согласование", 70, 70, False, False),
    ("Выиграна", 90, 100, True, False),
    ("Проиграна", 100, 0, False, True),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
        sa.Column("probability", sa.Integer()),
        sa.Column("is_won", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_lost", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.create_table(
        "deals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("stage_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.stages.id"),
                  nullable=False, index=True),
        # контрагент mgmt_accounting — по UUID без FK (модули не трогают чужие схемы)
        sa.Column("counterparty_id", UUID(as_uuid=True), index=True),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("erp_core.contacts.id")),
        sa.Column("responsible_id", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"), index=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8)),
        sa.Column("amount_base", sa.Numeric(20, 4)),
        sa.Column("expected_close_at", sa.Date()),
        sa.Column("lost_reason", sa.Text()),
        sa.Column("dimensions", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.execute(f"CREATE INDEX ix_deals_title_trgm ON {S}.deals "
               "USING gin (title gin_trgm_ops)")
    for name, position, probability, is_won, is_lost in STAGE_SEED:
        op.execute(
            f"INSERT INTO {S}.stages (id, name, position, probability, is_won, is_lost) "
            f"VALUES (gen_random_uuid(), '{name}', {position}, {probability}, "
            f"{is_won}, {is_lost})"
        )


def downgrade() -> None:
    op.drop_table("deals", schema=S)
    op.drop_table("stages", schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
