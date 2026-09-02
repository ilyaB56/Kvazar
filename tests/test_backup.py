"""Тесты системы бэкапов: retention (updates-and-backups-spec, этап A)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.config import get_settings
from src.core.backup import apply_retention
from src.core.models import Backup


@pytest.fixture(scope="module")
def db():
    from src.db import SessionLocal, engine

    try:
        engine.connect().close()
    except Exception:
        pytest.skip("PostgreSQL not available")
    yield SessionLocal


@pytest.mark.integration
def test_retention_keeps_last_n(db):
    """Сверх BACKUP_RETENTION удаляются самые старые (файл + строка)."""
    session = db()
    try:
        before = session.scalars(select(Backup).order_by(Backup.created_at)).all()
        base = datetime.now(UTC) - timedelta(days=100)
        for i in range(4):
            session.add(Backup(
                id=uuid.uuid4(),
                file_name=f"test-retention-{i}.dump.enc",
                size=1, sha256="0" * 64, kind="manual", status="created",
                created_at=base + timedelta(hours=i),
            ))
        session.commit()

        removed = apply_retention(session)
        limit = max(get_settings().backup_retention, 1)
        remaining = session.scalars(select(Backup).order_by(Backup.created_at)).all()
        assert len(remaining) == limit
        assert all(row.created_at >= before[-limit].created_at for row in remaining if before)
        assert removed >= 1
    finally:
        # чистим тестовые строки, чтобы не мешать реальным бэкапам
        for row in session.scalars(select(Backup).where(
                Backup.file_name.like("test-retention-%"))).all():
            session.delete(row)
        session.commit()
        session.close()
