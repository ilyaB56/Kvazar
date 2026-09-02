"""mini_crm: deals.won_at/lost_at (этап C — отчёт pipeline)

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mini_crm"


def upgrade() -> None:
    op.add_column("deals", sa.Column("won_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("deals", sa.Column("lost_at", sa.DateTime(timezone=True)), schema=S)


def downgrade() -> None:
    op.drop_column("deals", "lost_at", schema=S)
    op.drop_column("deals", "won_at", schema=S)
