"""Devtools этап A (devtools-spec §9.2, §10): RO-роль + пресеты таблиц

Revision ID: 0037
Revises: 0036

1. PG-роль erp_ro: GRANT USAGE/SELECT на все схемы + default privileges
   (новые таблицы покрываются). Дев-пароль erp_ro; прод меняет через
   ALTER ROLE вне миграции (env DATABASE_URL_RO).
2. erp_core.table_presets: личные пресеты браузера таблиц (решение
   владельца О2: фильтры+сортировка+колонки под именем, key-value на
   пользователя). UNIQUE(user_id, schema_name, table_name, name).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"
SCHEMAS = ("erp_core", "integrations", "mgmt_accounting", "mini_crm", "ai_agent")


def upgrade() -> None:
    # 1) RO-роль с грантами (IF NOT EXISTS-семантика через DO-блок)
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'erp_ro') THEN
                CREATE ROLE erp_ro LOGIN PASSWORD 'erp_ro';
            END IF;
        END
        $$;
    """)
    for schema in SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO erp_ro")
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO erp_ro")
        # будущие таблицы модулей покрываются автоматически
        op.execute(f"""
            ALTER DEFAULT PRIVILEGES IN SCHEMA {schema}
            GRANT SELECT ON TABLES TO erp_ro
        """)

    # 2) личные пресеты браузера (О2)
    op.create_table(
        "table_presets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("schema_name", sa.String(100), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        # {filters, sort, columns} — значение браузера (devtools §6.2)
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "schema_name", "table_name", "name",
                            name="uq_table_presets_user_table_name"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("table_presets", schema=S)
    for schema in SCHEMAS:
        op.execute(f"""
            ALTER DEFAULT PRIVILEGES IN SCHEMA {schema}
            REVOKE SELECT ON TABLES FROM erp_ro
        """)
        op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM erp_ro")
    op.execute("DROP ROLE IF EXISTS erp_ro")
