"""Мультитенантность B3: company_id в integrations (multitenancy-spec §5.3/Р4)

Revision ID: 0030
Revises: 0029

- connections: company_id NULL — NULL = платформенный коннектор (SMTP
  системных писем; видит/настраивает только супер-админ платформы)
- webhook_endpoints, sync_jobs, field_mappings, notification_rules,
  online_payments, item_mappings: company_id NOT NULL
- sync_runs / webhook_events / flow_runs — через родителя; recipes —
  глобальный каталог платформы (применение — через connection организации)
- backfill: существующие подключения/правила/платежи → «Основная»
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"
S = "integrations"

NOT_NULL = ["webhook_endpoints", "sync_jobs", "field_mappings",
            "notification_rules", "online_payments", "item_mappings"]
MAIN = f"(SELECT id FROM {CORE}.companies WHERE name = 'Основная')"


def upgrade() -> None:
    # connections: nullable (NULL = платформенный)
    op.add_column("connections",
                  sa.Column("company_id", UUID(as_uuid=True), nullable=True),
                  schema=S)
    op.execute(f"UPDATE {S}.connections SET company_id = {MAIN}"
               f" WHERE company_id IS NULL AND name <> 'ЦБ РФ'")

    for table in NOT_NULL:
        op.add_column(table, sa.Column("company_id", UUID(as_uuid=True),
                                       nullable=True), schema=S)
        op.execute(f"UPDATE {S}.{table} SET company_id = {MAIN}"
                   f" WHERE company_id IS NULL")
        op.alter_column(table, "company_id", nullable=False, schema=S)

    for table in NOT_NULL + ["connections"]:
        op.create_foreign_key(
            f"fk_{table}_company", table, "companies",
            ["company_id"], ["id"], source_schema=S, referent_schema=CORE)


def downgrade() -> None:
    for table in NOT_NULL + ["connections"]:
        op.drop_constraint(f"fk_{table}_company", table, schema=S)
        op.drop_column(table, "company_id", schema=S)
