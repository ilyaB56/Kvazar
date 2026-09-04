"""Шифрование секретов ядром (Fernet, симметричное).

Ключ — SECRETS_KEY окружения; один на систему: им же шифруются credentials
подключений (integrations) и серийные коды цифровых товаров (inventory).
Модули используют эти помощники напрямую, не импортируя чужие пакеты.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet

from src.config import get_settings


def fernet() -> Fernet:
    key = get_settings().secrets_key.encode()
    # Допускаем сырую строку любой длины: приводим к валидному Fernet-ключу.
    key = base64.urlsafe_b64encode(key.ljust(32, b"\0")[:32])
    return Fernet(key)


def encrypt_str(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt_str(blob: str) -> str:
    return fernet().decrypt(blob.encode()).decode()
