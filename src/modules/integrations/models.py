"""Модели модуля integrations — схема integrations (см. миграцию 0002)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base

S = "integrations"


class Connection(Base):
    __table_args__ = ({"schema": S},)
    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    connector_code: Mapped[str] = mapped_column(String(100), index=True)
    credentials_enc: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEndpoint(Base):
    __table_args__ = ({"schema": S},)
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    secret_token: Mapped[str] = mapped_column(String(128))
    target_module: Mapped[str] = mapped_column(String(100), default="external")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldMapping(Base):
    __table_args__ = ({"schema": S},)
    __tablename__ = "field_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    source_fields: Mapped[list] = mapped_column(JSONB, default=list)
    target_fields: Mapped[list] = mapped_column(JSONB, default=list)
    transformations: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncJob(Base):
    __table_args__ = ({"schema": S},)
    __tablename__ = "sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{S}.connections.id"))
    direction: Mapped[str] = mapped_column(String(10), default="fetch")
    cron: Mapped[str] = mapped_column(String(63), default="")
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    mapping_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{S}.field_mappings.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # планировщик (showcase-chain, этап B): последний момент постановки в очередь
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # спец-событие задания вместо дефолтного (этап C; ADR-002: имена событий — контракт)
    emit_event: Mapped[str] = mapped_column(String(100), default="integration.data.fetched")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncRun(Base):
    __table_args__ = ({"schema": S},)
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{S}.sync_jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    items_in: Mapped[int] = mapped_column(Integer, default=0)
    items_out: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Recipe(Base):
    """No-code рецепт: триггер -> маппинг -> действие (заготовка магазина интеграций)."""

    __table_args__ = ({"schema": S},)
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    definition: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationRule(Base):
    """notification_rules: событие → Telegram-сообщение (showcase-chain, этап D).

    template рендерится подстановкой {ключ} из payload (простые ключи
    верхнего уровня).
    """

    __table_args__ = ({"schema": S},)
    __tablename__ = "notification_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    event_name: Mapped[str] = mapped_column(String(100), index=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
