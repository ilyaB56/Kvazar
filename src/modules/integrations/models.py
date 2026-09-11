"""Модели модуля integrations — схема integrations (см. миграцию 0002)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, UniqueConstraint, Numeric
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
    # привязка к connection провайдера (sales-automation §4.1): приёмник
    # авторизует вебхук коннектором (verify_webhook / verify_by_fetch),
    # X-ERP-Token для таких endpoint'ов не требуется
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.connections.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvent(Base):
    """Журнал входящих вебхуков + идемпотентность (§4.2): дубли по
    (connection_id, external_key) отмечаются duplicate без обработки;
    invalid — подлинность не подтвердилась (тело храним для разбора)."""

    __table_args__ = (
        UniqueConstraint("connection_id", "external_key", name="uq_webhook_events_conn_external"),
        UniqueConstraint("endpoint_id", "external_key", name="uq_webhook_events_endpoint_external"),
        {"schema": S},
    )
    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.webhook_endpoints.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.connections.id", ondelete="SET NULL"))
    external_key: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100), default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new|processed|duplicate|invalid|error
    error: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class OnlinePayment(Base):
    """Нормализованный платёж провайдера (§4.3): UNIQUE(connection,
    provider_payment_id); buyer — ПДн, в API маскируется для ro и не
    попадает в события; UUID-ссылки на учёт без FK (паттерн crm_deal_id)."""

    __table_args__ = (
        UniqueConstraint("connection_id", "provider_payment_id",
                         name="uq_online_payments_conn_payment"),
        {"schema": S},
    )
    __tablename__ = "online_payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.connections.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50))
    provider_payment_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(12), default="received")  # received|processed|manual|ignored
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    buyer: Mapped[dict] = mapped_column(JSONB, default=dict)
    lines: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.webhook_events.id", ondelete="SET NULL"))
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_step: Mapped[str] = mapped_column(String(20), default="")
    error_reason: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ItemMapping(Base):
    """Маппинг сайт-товар → номенклатура (§4.4): точное совпадение
    external_item_id → sku; NULL connection = глобальный по sku."""

    __table_args__ = (
        UniqueConstraint("connection_id", "external_item_id",
                         name="uq_item_mappings_conn_external"),
        {"schema": S},
    )
    __tablename__ = "item_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.connections.id", ondelete="CASCADE"))
    external_item_id: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(100))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowRun(Base):
    """Шаговый журнал сценария (§4.5): UNIQUE(payment_id); retry
    продолжает с последнего успешного шага, перечитывая context
    (шаги идемпотентны: «уже есть order_id — пропустить»)."""

    __table_args__ = ({"schema": S},)
    __tablename__ = "flow_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.online_payments.id", ondelete="CASCADE"),
        unique=True, index=True)
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{S}.recipes.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|done|failed|manual
    step: Mapped[str] = mapped_column(String(14), default="")  # последний завершённый
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
