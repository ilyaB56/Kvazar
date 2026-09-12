"""Модели ядра — схема erp_core. Общие сущности, доступные всем модулям."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base

CORE_SCHEMA = "erp_core"


def _core(tablename: str) -> dict:
    return {"schema": CORE_SCHEMA, "tablename": tablename}


class User(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(
        String(50), ForeignKey(f"{CORE_SCHEMA}.roles.key", ondelete="RESTRICT"), default="user"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Версия токенов: += 1 при смене пароля — все ранее выданные токены умирают
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    """Роль доступа: ключ + отображаемое имя. admin — неизменяемая (rw везде)."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # цвет-акцент карточки роли в UI (матрица «Доступы и роли»)
    color: Mapped[str] = mapped_column(String(20), default="zinc")


class RolePermission(Base):
    """Право роли на модуль: 'rw' | 'ro'; отсутствие строки = 'none'."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "role_permissions"

    role_key: Mapped[str] = mapped_column(
        String(50), ForeignKey(f"{CORE_SCHEMA}.roles.key", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(30), primary_key=True)
    level: Mapped[str] = mapped_column(String(5))


class Company(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contact(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.companies.id"))
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Attachment(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    """events_log — журнал действий (аудит-трейл)."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[dict | str | int | bool | None] = mapped_column(JSONB)
    value_type: Mapped[str] = mapped_column(String(20), default="string")  # string|int|bool|json


class ModuleRegistry(Base):
    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "module_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[str] = mapped_column(String(20))
    db_schema: Mapped[str] = mapped_column(String(63))
    depends_on: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventOutbox(Base):
    """Надёжная доставка событий: пишем в outbox в той же транзакции, что и бизнес-данные,
    затем диспетчер рассылает подписчикам (Redis pub/sub + локальные обработчики)."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "event_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_name: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecordVersion(Base):
    """record_versions — журнал версий записей. Generic-таблица: пишется сервисными
    слоями модулей через общий хелпер core.versioning (переиспользуется в CRM и др.)."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "record_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    diff: Mapped[dict] = mapped_column(JSONB, default=dict)  # {поле: {old, new}}
    reason: Mapped[str | None] = mapped_column(Text)


class RevokedToken(Base):
    """revoked_tokens — blacklist отозванных refresh-токенов (по jti) до их exp."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "revoked_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthSession(Base):
    """auth_sessions — активные сеансы входа (sessions-security-spec §2.1).

    id = sid-клейм пары токенов; ротация refresh сохраняет id строки.
    «Активный» = revoked_at IS NULL и TTL (refresh_expire_days от
    created_at) не истёк."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id"), index=True)
    user_agent: Mapped[str] = mapped_column(Text, default="")  # обрезка 256 при записи
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Backup(Base):
    """backups — реестр резервных копий БД (шифруются Fernet по BACKUP_KEY)."""

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)  # размер зашифрованного файла, байт
    sha256: Mapped[str] = mapped_column(String(64))  # дайджест открытого дампа
    kind: Mapped[str] = mapped_column(String(20))  # manual | scheduled | pre_update
    status: Mapped[str] = mapped_column(String(20), default="created")  # created|verified|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiToken(Base):
    """api_tokens — служебные токены для машинных вызовов API (X-API-Token).

    Хранится только sha256-хэш; сам токен показывается один раз при создании.
    Роль работает как у пользователей (require_role проверяет .role).
    Действия токена записываются от имени владельца (owner_user_id) —
    created_by/аудит ссылаются на живого пользователя (FK users).
    """

    __table_args__ = ({"schema": CORE_SCHEMA},)
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(50), default="user")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{CORE_SCHEMA}.users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
