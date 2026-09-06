"""roles + role_permissions: матрица прав роли × модуль (редизайн §6.1)

Revision ID: 0020
Revises: 0019

Seed builtin-ролей повторяет текущее поведение доступа:
  admin    — rw везде (неизменяемая, в UI под замком);
  user     — rw: accounting, crm, integrations, ai; ro: system;
  readonly — ro везде.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CORE = "erp_core"

MODULES = ("accounting", "crm", "integrations", "ai", "system")

ROLES = [
    ("admin", "Администратор",
     "Полный доступ ко всем разделам и настройкам", "emerald"),
    ("user", "Пользователь",
     "Работа во всех разделах, настройка системы — только чтение", "teal"),
    ("readonly", "Только чтение",
     "Просмотр всех разделов без изменений", "amber"),
]

# role_key -> {module: level}; отсутствие модуля = none
MATRIX = {
    "admin": dict.fromkeys(MODULES, "rw"),
    "user": {"accounting": "rw", "crm": "rw", "integrations": "rw",
             "ai": "rw", "system": "ro"},
    "readonly": dict.fromkeys(MODULES, "ro"),
}


def upgrade() -> None:
    roles = op.create_table(
        "roles",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("color", sa.String(20), nullable=False, server_default="zinc"),
        schema=CORE,
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_key", sa.String(50),
                  sa.ForeignKey(f"{CORE}.roles.key", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("module", sa.String(30), primary_key=True),
        sa.Column("level", sa.String(5), nullable=False),
        schema=CORE,
    )
    op.bulk_insert(
        roles,
        [
            {"key": key, "name": name, "description": desc,
             "is_builtin": True, "color": color}
            for key, name, desc, color in ROLES
        ],
    )
    permissions = [
        {"role_key": role_key, "module": module, "level": level}
        for role_key, levels in MATRIX.items()
        for module, level in levels.items()
        if level != "none"
    ]
    op.bulk_insert(
        sa.table(
            "role_permissions",
            sa.column("role_key", sa.String),
            sa.column("module", sa.String),
            sa.column("level", sa.String),
            schema=CORE,
        ),
        permissions,
    )
    # users.role получает FK на roles.key: существующие значения уже сидированы
    op.create_foreign_key(
        "fk_users_role", "users", "roles",
        ["role"], ["key"],
        source_schema=CORE, referent_schema=CORE, ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_role", "users", schema=CORE, type_="foreignkey")
    op.drop_table("role_permissions", schema=CORE)
    op.drop_table("roles", schema=CORE)
