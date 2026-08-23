"""Манифест обновления: каноничный JSON, подпись/проверка Ed25519 (ADR-004).

Подпись — поверх каноничного JSON (sort_keys, без пробелов): одна функция
verify используется релиз-инструментом (tools/release.py) и оркестратором
(src.core.update — вызывается deploy/update.py через контейнер).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEFAULT_PUBLIC_KEY_PATH = Path(__file__).parents[3] / "deploy" / "keys" / "update-public.pem"


class ManifestError(Exception):
    """Манифест непрошёл проверку: причина в сообщении (для журнала/отказа)."""


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    """Каноничная сериализация — та же байтовая строка при подписи и проверке."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be a JSON object")
    return data


def generate_keypair(private_path: str | Path, public_path: str | Path) -> None:
    """Создать пару ключей выпуска (приватный — вне репо, публичный — в дистрибутив)."""
    private_key = Ed25519PrivateKey.generate()
    Path(private_path).write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    public_key = private_key.public_key()
    Path(public_path).write_bytes(public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ManifestError(f"Not an Ed25519 private key: {path}")
    return key


def load_public_key(path: str | Path | None = None) -> Ed25519PublicKey:
    pem_path = Path(path) if path else DEFAULT_PUBLIC_KEY_PATH
    if not pem_path.exists():
        raise ManifestError(f"Public key not found: {pem_path}")
    key = serialization.load_pem_public_key(pem_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ManifestError(f"Not an Ed25519 public key: {pem_path}")
    return key


def sign_manifest(manifest: dict[str, Any], private_key_path: str | Path) -> str:
    """Подписать манифест; вернуть base64-подпись (рядом кладут в manifest.sig)."""
    signature = load_private_key(private_key_path).sign(canonical_bytes(manifest))
    return base64.b64encode(signature).decode()


def verify_manifest(
    manifest: dict[str, Any],
    signature_b64: str | None,
    public_key_path: str | Path | None = None,
) -> None:
    """Единая проверка подписи. Raise ManifestError с понятной причиной.

    Используется: tools/release.py (релизная сторона), src.core.update.check
    (beat/UI), оркестратором deploy/update.py (через контейнер) — ДО действий.
    """
    if not signature_b64:
        raise ManifestError("Manifest is not signed (signature missing)")
    try:
        signature = base64.b64decode(signature_b64)
    except Exception as exc:
        raise ManifestError(f"Signature is not valid base64: {exc}") from exc
    try:
        load_public_key(public_key_path).verify(signature, canonical_bytes(manifest))
    except Exception as exc:
        raise ManifestError(
            "Signature verification failed: manifest corrupted or signed by another key"
        ) from exc


def read_signature(sig_path: str | Path) -> str | None:
    path = Path(sig_path)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None
