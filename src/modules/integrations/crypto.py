"""Шифрование секретов подключений (Fernet, симметричное)."""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet

from src.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().secrets_key.encode()
    # Допускаем сырую строку любой длины: приводим к валидному Fernet-ключу.
    key = base64.urlsafe_b64encode(key.ljust(32, b"\0")[:32])
    return Fernet(key)


def encrypt_dict(data: dict) -> str:
    import json

    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_dict(blob: str) -> dict:
    import json

    if not blob:
        return {}
    return json.loads(_fernet().decrypt(blob.encode()))
