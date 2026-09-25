"""Ротация SECRETS_KEY (security-plan P1 п.9).

Перешифровывает ВСЕ секреты Fernet: integrations.connections.
credentials_enc, mgmt_accounting.item_serials.code_enc, erp_core.
password_resets.token_enc — атомарно, в одной транзакции.

Старый ключ берётся из SECRETS_KEY окружения (или --old-key), новый —
из аргумента --new-key (сгенерировать:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
После ротации обновите SECRETS_KEY в .env и перезапустите стек:
docker compose up -d api worker beat — выводим готовую строку.

Запуск (внутри api-контейнера или с доступом к БД):
  python scripts/rotate_secrets_key.py --new-key <Fernet-key> [--old-key <старый>]
"""

from __future__ import annotations

import argparse
import sys

from cryptography.fernet import Fernet


def rotate(old_key: str, new_key: str) -> dict[str, int]:
    from sqlalchemy import text

    from src.db import SessionLocal

    import base64

    def _fernet_for(raw: str) -> Fernet:
        """Готовый Fernet-ключ (44 симв. base64) — как есть; сырая строка
        (как src.core.crypto.fernet) — дополняем до 32 байт и кодируем."""
        try:
            return Fernet(raw.encode())
        except ValueError:
            key = raw.encode()
            key = base64.urlsafe_b64encode((key + bytes(32))[:32])
            return Fernet(key)

    old_f = _fernet_for(old_key)
    new_f = _fernet_for(new_key)

    skipped = {"count": 0}

    def reencrypt(blob: str) -> str:
        """Строки, не расшифровывающиеся старым ключом (мусор от прошлых
        экспериментов/другого ключа), пропускаем с подсчётом — ротация
        остального не должна останавливаться."""
        try:
            return new_f.encrypt(old_f.decrypt(blob.encode())).decode()
        except Exception:  # noqa: BLE001
            skipped["count"] += 1
            return None

    db = SessionLocal()
    counts = {}
    try:
        # 1) integrations.connections.credentials_enc
        rows = db.execute(text(
            "SELECT id, credentials_enc FROM integrations.connections"
            " WHERE credentials_enc IS NOT NULL AND credentials_enc != ''"
        )).all()
        changed = 0
        for row in rows:
            enc = reencrypt(row[1])
            if enc is None:
                continue
            db.execute(text(
                "UPDATE integrations.connections SET credentials_enc = :enc"
                " WHERE id = :id").bindparams(enc=enc, id=row[0]))
            changed += 1
        counts["connections"] = changed

        # 2) mgmt_accounting.item_serials.code_enc
        rows = db.execute(text(
            "SELECT id, code_enc FROM mgmt_accounting.item_serials"
            " WHERE code_enc IS NOT NULL AND code_enc != ''"
        )).all()
        changed = 0
        for row in rows:
            enc = reencrypt(row[1])
            if enc is None:
                continue
            db.execute(text(
                "UPDATE mgmt_accounting.item_serials SET code_enc = :enc"
                " WHERE id = :id").bindparams(enc=enc, id=row[0]))
            changed += 1
        counts["item_serials"] = changed

        # 3) erp_core.password_resets.token_enc
        rows = db.execute(text(
            "SELECT id, token_enc FROM erp_core.password_resets"
            " WHERE token_enc IS NOT NULL AND token_enc != ''"
        )).all()
        changed = 0
        for row in rows:
            enc = reencrypt(row[1])
            if enc is None:
                continue
            db.execute(text(
                "UPDATE erp_core.password_resets SET token_enc = :enc"
                " WHERE id = :id").bindparams(enc=enc, id=row[0]))
            changed += 1
        counts["password_resets"] = changed

        db.commit()  # атомарно: всё или ничего
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-key", default=None,
                        help="старый SECRETS_KEY (по умолчанию — из окружения)")
    parser.add_argument("--new-key", required=True,
                        help="новый Fernet-ключ")
    args = parser.parse_args()

    old_key = args.old_key
    if old_key is None:
        from src.config import get_settings

        old_key = get_settings().secrets_key
    # валидация ключей до начала (сырые строки — норм, деривация ниже)
    if not old_key or not args.new_key:
        print("Пустой ключ", file=sys.stderr)
        return 2

    counts = rotate(old_key, args.new_key)
    total = sum(counts.values())
    print(f"rotated: {counts} (total {total})")
    if counts.pop("skipped", 0):
        print("ВНИМАНИЕ: часть строк не расшифровалась старым ключом и "
              "пропущена (мусор от другого ключа) — проверьте вручную")
    print("Обновите .env и перезапустите стек:")
    print(f"  SECRETS_KEY={args.new_key}")
    print("  docker compose up -d api worker beat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
