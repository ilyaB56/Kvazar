"""Шифрование секретов подключений (Fernet, симметричное).

Сам Fernet — в src/core/crypto.py (ключ SECRETS_KEY общий на систему);
здесь — словарная обёртка credentials подключений.
"""

from __future__ import annotations

import json

from src.core.crypto import decrypt_str, encrypt_str


def encrypt_dict(data: dict) -> str:
    return encrypt_str(json.dumps(data))


def decrypt_dict(blob: str) -> dict:
    if not blob:
        return {}
    return json.loads(decrypt_str(blob))
