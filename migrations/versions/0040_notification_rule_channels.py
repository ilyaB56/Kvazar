"""Notifications этап C (notifications-spec §5.3/§12): channels в notification_rules

Revision ID: 0040
Revises: 0039

- JSONB NOT NULL DEFAULT '["telegram"]' — существующие правила не меняют
  поведения (только Telegram);
- '["in_app","telegram"]' — оба канала: in_app-потребитель создаёт
  строки через notify(); активные правила по (событие × организация) без
  in_app глушат in-app-создание.
Downgrade: drop column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_rules",
        sa.Column("channels", JSONB(), nullable=False,
                  server_default='["telegram"]'),
        schema="integrations",
    )


def downgrade() -> None:
    op.drop_column("notification_rules", "channels", schema="integrations")
