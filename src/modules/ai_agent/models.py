"""Модели модуля ai_agent — схема ai_agent (дополняется по этапам C/E)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from src.db import Base

SCHEMA = "ai_agent"


class Vector1024(UserDefinedType):
    """Тип pgvector vector(1024); в Python — список float (JSON-строкой в БД)."""

    cache_ok = True

    def get_col_spec(self) -> str:
        return "VECTOR(1024)"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(f"{v:.6f}" for v in value) + "]"
        return process


class Document(Base):
    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(10), default="file")  # file|csv
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("erp_core.users.id"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector1024)


class ChatSession(Base):
    """Сессия диалога; chat_messages — полное логирование (ADR-006 п.7)."""

    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("erp_core.users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __table_args__ = ({"schema": SCHEMA},)
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # sources, tools_used
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
