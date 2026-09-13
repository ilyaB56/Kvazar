"""Мультитенантность A: организации + пользователи (multitenancy-spec §5.1-5.2)

Revision ID: 0027
Revises: 0026

- companies.is_active (false = вход пользователей организации запрещён)
- users.company_id (FK, NULL только у платформенного админа) +
  users.is_platform_admin + CHECK (company_id IS NULL) = is_platform_admin
  (двусторонний: у платформенного админа нет организации, у обычного
  организация обязательна — строже формулировки задания, по §4.1/§5.2 спеки)
- settings.company_id NULL (NULL = платформенная, фолбэк org → платформа, Р3)
- events_log.company_id NULL (заполняется издателем; этап B)

Backfill: создаётся организация «Основная»; все пользователи, кроме
admin@example.com, получают её company_id; admin@example.com →
is_platform_admin=true (без организации). Существующие настройки
остаются платформенными дефолтами (Р3), журналы — платформенными.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "erp_core"
PLATFORM_ADMIN_EMAIL = "admin@example.com"


def upgrade() -> None:
    op.add_column("companies",
                  sa.Column("is_active", sa.Boolean(), nullable=False,
                            server_default="true"),
                  schema=S)
    op.add_column("users",
                  sa.Column("company_id", UUID(as_uuid=True), nullable=True),
                  schema=S)
    op.add_column("users",
                  sa.Column("is_platform_admin", sa.Boolean(), nullable=False,
                            server_default="false"),
                  schema=S)
    op.add_column("settings",
                  sa.Column("company_id", UUID(as_uuid=True), nullable=True),
                  schema=S)
    op.add_column("events_log",
                  sa.Column("company_id", UUID(as_uuid=True), nullable=True),
                  schema=S)

    # backfill ДО CHECK: организация «Основная» + распределение пользователей
    op.execute(f"""
        INSERT INTO {S}.companies (id, name, inn, is_active, created_at)
        SELECT gen_random_uuid(), 'Основная', '', true, now()
        WHERE NOT EXISTS (SELECT 1 FROM {S}.companies WHERE name = 'Основная')
    """)
    op.execute(f"""
        UPDATE {S}.users
        SET company_id = (SELECT id FROM {S}.companies WHERE name = 'Основная')
        WHERE email <> '{PLATFORM_ADMIN_EMAIL}' AND company_id IS NULL
    """)
    op.execute(f"""
        UPDATE {S}.users
        SET is_platform_admin = true
        WHERE email = '{PLATFORM_ADMIN_EMAIL}'
    """)

    op.create_foreign_key(
        "fk_users_company", "users", "companies",
        ["company_id"], ["id"], source_schema=S, referent_schema=S)
    op.create_foreign_key(
        "fk_settings_company", "settings", "companies",
        ["company_id"], ["id"], source_schema=S, referent_schema=S)
    op.create_foreign_key(
        "fk_events_log_company", "events_log", "companies",
        ["company_id"], ["id"], source_schema=S, referent_schema=S)
    # двусторонний инвариант (§5.2): платформенный админ — без организации,
    # обычный пользователь — обязательно с организацией
    op.create_check_constraint(
        "users_company_or_platform_check", "users",
        "(company_id IS NULL) = is_platform_admin", schema=S)


def downgrade() -> None:
    op.drop_constraint("users_company_or_platform_check", "users", schema=S)
    op.drop_constraint("fk_events_log_company", "events_log", schema=S)
    op.drop_constraint("fk_settings_company", "settings", schema=S)
    op.drop_constraint("fk_users_company", "users", schema=S)
    op.drop_column("events_log", "company_id", schema=S)
    op.drop_column("settings", "company_id", schema=S)
    op.drop_column("users", "is_platform_admin", schema=S)
    op.drop_column("users", "company_id", schema=S)
    op.drop_column("companies", "is_active", schema=S)
    # организация «Основная» остаётся строкой справочника: безвредна и
    # сохраняет историю (contacts.company_id может ссылаться)
