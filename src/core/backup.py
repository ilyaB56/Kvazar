"""Система бэкапов БД (updates-and-backups-spec, этап A; ADR-004/005).

create_backup: pg_dump (custom -Fc) через subprocess с клиентом в образе
api/beat, подключение к контейнеру db по сети; шифрование Fernet по
BACKUP_KEY (отдельному от SECRETS_KEY — ротация SecretsKey не ломает старые
бэкапы); строка в erp_core.backups; аудит backup.created.

restore_backup — расшифровка + pg_restore; production-restore выполняет
хост-скрипт deploy/update.py, отсюда — восстановление в служебную БД
erp_verify для проверок. Откат данными через alembic downgrade запрещён
(ADR-004): только восстановление бэкапа.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core.models import AuditEvent, Backup
from src.db import SessionLocal

logger = logging.getLogger(__name__)

VERIFY_DB = "erp_verify"


class BackupError(Exception):
    """Сбой операции бэкапа; сообщение показывается в API/журнале."""


def _settings():
    return get_settings()


def _fernet() -> Fernet:
    return Fernet(_settings().backup_key.encode())


def _backup_dir() -> Path:
    directory = Path(_settings().backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _db_container_url() -> str:
    """URL для pg_dump/pg_restore: отдельная утилита, не SQLAlchemy-движок."""
    url = _settings().database_url
    return url.replace("+psycopg2", "")


def _psql_admin_url() -> str:
    """URL системной базы postgres — для CREATE/DROP erp_verify."""
    url = _db_container_url()
    return url.rsplit("/", 1)[0] + "/postgres"


def create_backup(kind: str = "manual", user_id: uuid.UUID | None = None) -> Backup:
    if kind not in ("manual", "scheduled", "pre_update"):
        raise BackupError(f"Unknown backup kind: {kind}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    file_name = f"erp-{stamp}-{kind}.dump.enc"
    plain_path = _backup_dir() / f"erp-{stamp}-{kind}.dump"

    dump_cmd = [
        "pg_dump", _db_container_url(), "--format=custom", "--no-password",
        "--file", str(plain_path),
    ]
    try:
        subprocess.run(dump_cmd, check=True, capture_output=True, timeout=600)
    except FileNotFoundError as exc:
        raise BackupError("pg_dump not found in image (postgresql-client required)") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace")[:500] if exc.stderr else ""
        raise BackupError(f"pg_dump failed: {detail}") from exc

    # sha256 считается ДО шифрования (spec): проверка целостности содержимого
    sha256 = hashlib.sha256(plain_path.read_bytes()).hexdigest()
    encrypted = _fernet().encrypt(plain_path.read_bytes())
    enc_path = _backup_dir() / file_name
    enc_path.write_bytes(encrypted)
    plain_path.unlink()

    db = SessionLocal()
    try:
        backup = Backup(
            file_name=file_name,
            size=enc_path.stat().st_size,
            sha256=sha256,
            kind=kind,
            status="created",
        )
        db.add(backup)
        db.add(AuditEvent(
            user_id=user_id, action="backup.created",
            entity_type="backup", entity_id=file_name,
        ))
        db.commit()
        db.refresh(backup)
        apply_retention(db)
        return backup
    finally:
        db.close()


def apply_retention(db: Session | None = None) -> int:
    """Оставить последние BACKUP_RETENTION бэкапов (файл + строка).

    Виды не делим — просто N последних по времени (spec). Возвращает число удалённых.
    """
    own_session = db is None
    session = db or SessionLocal()
    try:
        limit = max(_settings().backup_retention, 1)
        keep_ids = {
            row.id for row in session.scalars(
                select(Backup).order_by(Backup.created_at.desc(), Backup.id.desc()).limit(limit)
            ).all()
        }
        removed = 0
        for row in session.scalars(select(Backup).order_by(Backup.created_at)).all():
            if row.id in keep_ids:
                continue
            path = _backup_dir() / row.file_name
            path.unlink(missing_ok=True)
            session.delete(row)
            removed += 1
        if removed:
            session.commit()
        return removed
    finally:
        if own_session:
            session.close()


def decrypt_to_file(backup: Backup, target: Path) -> Path:
    """Расшифровать бэкап в файл (используется verify и хост-восстановлением)."""
    enc_path = _backup_dir() / backup.file_name
    if not enc_path.exists():
        raise BackupError(f"Backup file missing: {backup.file_name}")
    try:
        plain = _fernet().decrypt(enc_path.read_bytes())
    except Exception as exc:  # неверный ключ/битый формат
        raise BackupError(f"Decrypt failed (wrong BACKUP_KEY or corrupted file): {exc}") from exc
    target.write_bytes(plain)
    return target


def _recreate_verify_db() -> str:
    """Пересоздать служебную БД erp_verify (для test-restore)."""
    drop = ["psql", _psql_admin_url(), "--no-password", "-c",
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{VERIFY_DB}'"]
    subprocess.run(drop, check=True, capture_output=True, timeout=60)
    subprocess.run(
        ["psql", _psql_admin_url(), "--no-password", "-c",
         f"DROP DATABASE IF EXISTS {VERIFY_DB} WITH (FORCE)"],
        check=True, capture_output=True, timeout=60,
    )
    subprocess.run(
        ["psql", _psql_admin_url(), "--no-password", "-c", f"CREATE DATABASE {VERIFY_DB}"],
        check=True, capture_output=True, timeout=60,
    )
    return VERIFY_DB


def restore_backup(backup_id: uuid.UUID | str, target_db: str = VERIFY_DB) -> dict:
    """Расшифровать и восстановить бэкап в target_db (по умолчанию erp_verify).

    Production-восстановление в основную БД выполняет хост-скрипт
    deploy/update.py (этап C) — контейнер не должен уметь ронять живую БД.
    """
    db = SessionLocal()
    try:
        backup = db.get(Backup, uuid.UUID(str(backup_id)))
        if backup is None:
            raise BackupError(f"Backup not found: {backup_id}")
        plain_path = _backup_dir() / f"verify-{backup.file_name}.dump"
        decrypt_to_file(backup, plain_path)
        try:
            if target_db == VERIFY_DB:
                _recreate_verify_db()
            subprocess.run(
                ["pg_restore", "--no-password", "--dbname",
                 _db_container_url().rsplit("/", 1)[0] + f"/{target_db}",
                 str(plain_path)],
                check=True, capture_output=True, timeout=600,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode(errors="replace")[:500] if exc.stderr else ""
            raise BackupError(f"pg_restore failed: {detail}") from exc
        finally:
            plain_path.unlink(missing_ok=True)
        return {"ok": True, "restored_to": target_db}
    finally:
        db.close()


def verify_backup(backup_id: uuid.UUID | str) -> Backup:
    """Проверка: ключ/формат живы + sha256 + test-restore в erp_verify + sanity.

    Итог — статус verified/failed в erp_core.backups.
    """
    db = SessionLocal()
    try:
        backup = db.get(Backup, uuid.UUID(str(backup_id)))
        if backup is None:
            raise BackupError(f"Backup not found: {backup_id}")
        plain_path = _backup_dir() / f"verify-{backup.file_name}.dump"
        try:
            decrypt_to_file(backup, plain_path)
            sha256 = hashlib.sha256(plain_path.read_bytes()).hexdigest()
            if sha256 != backup.sha256:
                raise BackupError(f"sha256 mismatch: {sha256} != {backup.sha256}")
            restore_backup(backup_id)
            count = subprocess.run(
                ["psql", _db_container_url().rsplit("/", 1)[0] + f"/{VERIFY_DB}",
                 "--no-password", "-t", "-c", "SELECT count(*) FROM erp_core.users"],
                check=True, capture_output=True, timeout=60,
            ).stdout.decode().strip()
            logger.info("backup %s verified: users=%s", backup.file_name, count)
            backup.status = "verified"
        except Exception as exc:
            logger.warning("backup %s verify failed: %s", backup.file_name, exc)
            backup.status = "failed"
            raise BackupError(f"Verify failed: {exc}") from exc
        finally:
            plain_path.unlink(missing_ok=True)
            db.commit()
        return backup
    finally:
        db.close()
