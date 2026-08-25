"""sync_jobs.last_run_at + emit_event: планировщик и спец-события заданий

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    # этап B: момент последней постановки в очередь (анти-дубли планировщика)
    op.add_column("sync_jobs",
                  sa.Column("last_run_at", sa.DateTime(timezone=True)), schema=S)
    # этап C: событие, которое публикует run_job вместо дефолтного
    op.add_column("sync_jobs",
                  sa.Column("emit_event", sa.String(100), nullable=False,
                            server_default="integration.data.fetched"), schema=S)


def downgrade() -> None:
    op.drop_column("sync_jobs", "emit_event", schema=S)
    op.drop_column("sync_jobs", "last_run_at", schema=S)
