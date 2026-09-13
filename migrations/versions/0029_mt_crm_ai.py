"""Мультитенантность B2: company_id в mini_crm и ai_agent (multitenancy-spec §5.3)

Revision ID: 0029
Revises: 0028

- mini_crm: stages + company_id NOT NULL (UNIQUE(position) →
  (company_id, position)); deals + company_id NOT NULL;
  communications/activities — через deal (без колонок)
- ai_agent: documents/chat_sessions/proposals + company_id NOT NULL;
  chunks — без колонки (RAG-поиск фильтруется join по documents)
- backfill: все существующие данные → «Основная»
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"
CRM = "mini_crm"
AI = "ai_agent"

TABLES = [
    (CRM, "stages"), (CRM, "deals"),
    (AI, "documents"), (AI, "chat_sessions"), (AI, "proposals"),
]
MAIN = f"(SELECT id FROM {CORE}.companies WHERE name = 'Основная')"


def upgrade() -> None:
    for schema, table in TABLES:
        op.add_column(table, sa.Column("company_id", UUID(as_uuid=True),
                                       nullable=True), schema=schema)
        op.execute(f"UPDATE {schema}.{table} SET company_id = {MAIN}"
                   f" WHERE company_id IS NULL")
        op.create_foreign_key(
            f"fk_{table}_company", table, "companies",
            ["company_id"], ["id"], source_schema=schema, referent_schema=CORE)
        op.alter_column(table, "company_id", nullable=False, schema=schema)

    op.drop_constraint("stages_position_key", "stages", schema=CRM, type_="unique")
    op.create_unique_constraint("uq_stages_company_position", "stages",
                                ["company_id", "position"], schema=CRM)


def downgrade() -> None:
    op.drop_constraint("uq_stages_company_position", "stages", schema=CRM)
    op.create_unique_constraint("stages_position_key", "stages", ["position"],
                                schema=CRM)
    for schema, table in reversed(TABLES):
        op.drop_constraint(f"fk_{table}_company", table, schema=schema)
        op.drop_column(table, "company_id", schema=schema)
