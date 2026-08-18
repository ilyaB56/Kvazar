"""integrations: схема и таблицы интеграционной платформы

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")

    op.create_table(
        "connector_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("capabilities", JSONB(), nullable=False, server_default='["fetch","push"]'),
        sa.Column("config_schema", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )
    op.create_table(
        "connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector_code", sa.String(100), nullable=False, index=True),
        # Секреты шифруются Fernet и хранятся как текст: {encrypted blob}
        sa.Column("credentials_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_check_ok", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret_token", sa.String(128), nullable=False),
        # Источник события: external = любой отправитель с верным токеном
        sa.Column("target_module", sa.String(100), nullable=False, server_default="external"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "field_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_fields", JSONB(), nullable=False, server_default="[]"),
        sa.Column("target_fields", JSONB(), nullable=False, server_default="[]"),
        sa.Column("transformations", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "sync_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default="fetch"),  # fetch|push
        sa.Column("cron", sa.String(63), nullable=False, server_default=""),
        sa.Column("endpoint", sa.String(500), nullable=False, server_default=""),
        sa.Column("mapping_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.field_mappings.id")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sync_job_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.sync_jobs.id"),
                  nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),  # success|error
        sa.Column("items_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "recipes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        # no-code конструктор: {trigger: {...}, mapping_id, action: {...}}
        sa.Column("definition", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )


def downgrade() -> None:
    for table in ("recipes", "sync_runs", "sync_jobs", "field_mappings",
                  "webhook_endpoints", "connections", "connector_types"):
        op.drop_table(table, schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
