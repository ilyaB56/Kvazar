"""CLI бэкапов: python -m src.backup create --kind pre_update|manual|scheduled
              python -m src.backup verify <backup_id>
              python -m src.backup restore --backup-id ID [--target erp] [--drop]

Используется оркестратором обновления (deploy/update.py) через
`docker compose exec api python -m src.backup ...`; restore в основную БД
(--target erp --drop) — путь авто-отката: расшифровка + пересоздание БД +
pg_restore (откат данными через alembic downgrade запрещён, ADR-004).
"""

from __future__ import annotations

import argparse
import json
import sys

from src.core.backup import (
    VERIFY_DB,
    BackupError,
    create_backup,
    decrypt_to_file,
    restore_backup,
    verify_backup,
)
from src.db import SessionLocal
from src.core.models import Backup


def _restore_production(backup_id: str) -> int:
    """Восстановление в основную БД: приложение должно быть остановлено."""
    import subprocess

    from src.core.backup import _db_container_url, _psql_admin_url

    db = SessionLocal()
    try:
        backup = db.get(Backup, backup_id)
        if backup is None:
            raise BackupError(f"Backup not found: {backup_id}")
        plain_path = decrypt_to_file(backup, _backup_tmp(backup))
    finally:
        db.close()
    try:
        subprocess.run(
            ["psql", _psql_admin_url(), "--no-password", "-c",
             "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'erp'"],
            check=True, capture_output=True, timeout=60)
        subprocess.run(
            ["psql", _psql_admin_url(), "--no-password", "-c",
             "DROP DATABASE IF EXISTS erp WITH (FORCE)"],
            check=True, capture_output=True, timeout=60)
        subprocess.run(
            ["psql", _psql_admin_url(), "--no-password", "-c", "CREATE DATABASE erp"],
            check=True, capture_output=True, timeout=60)
        subprocess.run(
            ["pg_restore", "--no-password", "--dbname", _db_container_url(), str(plain_path)],
            check=True, capture_output=True, timeout=1800)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace")[:500] if exc.stderr else ""
        raise BackupError(f"production restore failed: {detail}") from exc
    finally:
        plain_path.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "restored_to": "erp", "backup_id": backup_id}))
    return 0


def _backup_tmp(backup):
    from src.core.backup import _backup_dir

    return _backup_dir() / f"restore-{backup.file_name}.dump"


def main() -> int:
    parser = argparse.ArgumentParser(prog="src.backup")
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create")
    create_parser.add_argument("--kind", default="manual",
                               choices=["manual", "scheduled", "pre_update"])

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("backup_id")

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--backup-id", required=True)
    restore_parser.add_argument("--target", default=VERIFY_DB)
    restore_parser.add_argument("--drop", action="store_true",
                                help="пересоздать целевую БД (путь авто-отката)")

    args = parser.parse_args()
    try:
        if args.command == "create":
            backup = create_backup(kind=args.kind)
            # машиночитаемая строка для оркестратора: дождаться записи в БД
            print(json.dumps({
                "id": str(backup.id), "file_name": backup.file_name,
                "kind": backup.kind, "status": backup.status,
            }))
            return 0
        if args.command == "verify":
            backup = verify_backup(args.backup_id)
            print(json.dumps({"id": str(backup.id), "status": backup.status}))
            return 0 if backup.status == "verified" else 1
        if args.command == "restore":
            if args.drop or args.target == "erp":
                return _restore_production(args.backup_id)
            result = restore_backup(args.backup_id, target_db=args.target)
            print(json.dumps(result))
            return 0
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
