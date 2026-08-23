"""Тесты манифеста обновлений: подпись Ed25519, verify, каноничность (этап B)."""

from __future__ import annotations

import pytest

from src.core.update.manifest import (
    ManifestError,
    canonical_bytes,
    generate_keypair,
    sign_manifest,
    verify_manifest,
)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keys")
    private, public = directory / "priv.pem", directory / "pub.pem"
    generate_keypair(private, public)
    return private, public


def sample_manifest() -> dict:
    return {
        "version": "0.1.1",
        "channel": "stable",
        "changelog": "Исправления",
        "min_supported": "0.1.0",
        "images": {"api": {"repo": "erp-api:0.1.1", "digest": "sha256:abc"}},
        "created_at": "2026-08-22T00:00:00+00:00",
    }


def test_sign_and_verify_ok(keys):
    private, public = keys
    manifest = sample_manifest()
    signature = sign_manifest(manifest, private)
    verify_manifest(manifest, signature, public)  # не бросает


def test_verify_tampered_manifest(keys):
    private, public = keys
    manifest = sample_manifest()
    signature = sign_manifest(manifest, private)
    tampered = dict(manifest, version="9.9.9")
    with pytest.raises(ManifestError, match="corrupted|another key"):
        verify_manifest(tampered, signature, public)


def test_verify_wrong_key(tmp_path, keys):
    _, public = keys
    other_private = tmp_path / "other_priv.pem"
    other_public = tmp_path / "other_pub.pem"
    generate_keypair(other_private, other_public)
    signature = sign_manifest(sample_manifest(), other_private)
    # подпись чужой парой против нашего публичного ключа — отказ
    with pytest.raises(ManifestError, match="corrupted|another key"):
        verify_manifest(sample_manifest(), signature, public)


def test_verify_missing_signature(keys):
    _, public = keys
    with pytest.raises(ManifestError, match="not signed"):
        verify_manifest(sample_manifest(), None, public)


def test_canonical_json_order_independent():
    first = {"a": 1, "b": {"x": 1, "y": 2}}
    second = {"b": {"y": 2, "x": 1}, "a": 1}
    assert canonical_bytes(first) == canonical_bytes(second)
