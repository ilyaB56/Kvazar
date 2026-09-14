"""TOTP-слой ядра (multitenancy-spec §5.4): единая точка pyotp (banned-api
закрывает импорт из модулей). Секрет — Fernet по SECRETS_KEY (core/crypto);
резервные коды — sha256-хэши (как api_tokens), формат «XXXX-XXXX» без
неоднозначных символов; окно ±1 шаг (±30 с)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

import pyotp
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.crypto import decrypt_str, encrypt_str
from src.core.models import TotpBackupCode, UserTotp

# без неоднозначных символов (0/O, 1/I/L)
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_WORDS = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, email: str, issuer: str = "Kvazar ERP") -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    """TOTP с окном ±1 шаг (±30 с)."""
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def store_secret(db: Session, user_id: uuid.UUID, secret: str) -> UserTotp:
    """Записать (или перезаписать неподтверждённый) секрет настройки."""
    row = db.get(UserTotp, user_id)
    if row is None or row.enabled_at is None:
        row = row or UserTotp(user_id=user_id, secret_enc=encrypt_str(secret))
        row.secret_enc = encrypt_str(secret)
        row.confirmed_at = None
        row.enabled_at = None
        db.add(row)
    else:
        # включённую 2FA перезаписывать setup'ом нельзя — только сброс платформой
        raise PermissionError("totp_already_enabled")
    db.flush()
    return row


def load_secret(db: Session, user_id: uuid.UUID) -> str | None:
    row = db.get(UserTotp, user_id)
    if row is None:
        return None
    return decrypt_str(row.secret_enc)


def confirm(db: Session, user_id: uuid.UUID) -> list[str]:
    """Подтвердить секрет и включить 2FA; вернуть резервные коды (один раз)."""
    row = db.get(UserTotp, user_id)
    if row is None or row.enabled_at is not None:
        raise PermissionError("totp_not_in_setup")
    now = datetime.now(UTC)
    row.confirmed_at = now
    row.enabled_at = now
    codes = ["-".join("".join(secrets.choice(_ALPHABET) for _ in range(4))
                       for _ in range(2)) for _ in range(_CODE_WORDS)]
    # старые неотspent коды (повторный setup после сброса) заменяются
    for old in db.scalars(select(TotpBackupCode).where(
            TotpBackupCode.user_id == user_id,
            TotpBackupCode.used_at.is_(None))).all():
        db.delete(old)
    db.add_all([TotpBackupCode(
        user_id=user_id, code_hash=_hash(code)) for code in codes])
    db.flush()
    return codes


def is_enabled(db: Session, user_id: uuid.UUID) -> bool:
    row = db.get(UserTotp, user_id)
    return row is not None and row.enabled_at is not None


def is_setup_pending(db: Session, user_id: uuid.UUID) -> bool:
    row = db.get(UserTotp, user_id)
    return row is not None and row.enabled_at is None


def check_backup_code(db: Session, user_id: uuid.UUID, code: str) -> bool:
    """Резервный код: сверка по хэшу, погашение used_at (одноразовость)."""
    hashed = _hash(code.strip().upper())
    row = db.scalar(select(TotpBackupCode).where(
        TotpBackupCode.user_id == user_id,
        TotpBackupCode.code_hash == hashed,
        TotpBackupCode.used_at.is_(None),
    ).limit(1))
    if row is None:
        return False
    row.used_at = datetime.now(UTC)
    db.flush()
    return True


def verify_login_code(db: Session, user_id: uuid.UUID, code: str) -> bool:
    """Код входа: TOTP или резервный (гасится)."""
    secret = load_secret(db, user_id)
    if secret and verify_code(secret, code):
        return True
    return check_backup_code(db, user_id, code)


def reset(db: Session, user_id: uuid.UUID) -> None:
    """Сброс 2FA платформой: секрет и коды удаляются (обязательность
    сохраняется — следующий вход руководителя запустит setup заново)."""
    row = db.get(UserTotp, user_id)
    if row is not None:
        db.delete(row)
    for code in db.scalars(select(TotpBackupCode).where(
            TotpBackupCode.user_id == user_id)).all():
        db.delete(code)
    db.flush()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
