"""ai_agent: extension vector + documents/chunks (RAG, этап B)

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "ai_agent"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(10), nullable=False, server_default="file"),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("erp_core.users.id"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=S,
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.documents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.dialects.postgresql.JSONB(), nullable=True),  # заменяется ниже
        schema=S,
    )
    op.execute(f"ALTER TABLE {S}.chunks ALTER COLUMN embedding TYPE vector(1024) USING NULL")
    # HNSW, а не ivfflat: ivfflat на пустой таблице даёт пустой приблизительный
    # поиск; HNSW корректно строится на пустой и обновляется при вставках
    op.execute(
        f"CREATE INDEX ix_chunks_embedding ON {S}.chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("chunks", schema=S)
    op.drop_table("documents", schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
