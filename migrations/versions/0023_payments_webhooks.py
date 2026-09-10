"""Онлайн-оплата A: webhook_endpoints.connection_id + webhook_events (sales-automation §4.1–4.2)

Revision ID: 0023
Revises: 0022

endpoint с connection_id авторизуется коннектором провайдера
(verify_webhook/verify_by_fetch), X-ERP-Token не требуется; журнал
webhook_events даёт идемпотентность приёма (UNIQUE connection+external_key).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "integrations"


def upgrade() -> None:
    op.add_column(
        "webhook_endpoints",
        sa.Column("connection_id", UUID(as_uuid=True), nullable=True),
        schema=S,
    )
    op.create_foreign_key(
        "fk_webhook_endpoints_connection", "webhook_endpoints", "connections",
        ["connection_id"], ["id"], source_schema=S, referent_schema=S,
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("endpoint_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.webhook_endpoints.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # NULL оставлен для generic-приёмника без connection (P1: журнал и там)
        sa.Column("connection_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.connections.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False, default=""),
        sa.Column("payload", JSONB, nullable=False, default=dict),
        sa.Column("status", sa.String(12), nullable=False, default="new"),
        sa.Column("error", sa.Text, nullable=False, default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        # идемпотентность: один внешний ключ на connection (спека §3.4)
        sa.UniqueConstraint("connection_id", "external_key",
                            name="uq_webhook_events_conn_external"),
        # без connection (generic) — тоже не плодим дубли на endpoint
        sa.UniqueConstraint("endpoint_id", "external_key",
                            name="uq_webhook_events_endpoint_external"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("webhook_events", schema=S)
    op.drop_constraint("fk_webhook_endpoints_connection", "webhook_endpoints",
                       schema=S, type_="foreignkey")
    op.drop_column("webhook_endpoints", "connection_id", schema=S)
