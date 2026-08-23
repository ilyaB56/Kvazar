"""Релизные инструменты (updates-and-backups-spec, этап B). Наша сторона,
клиенту не поставляется.

Команды:
  keygen --private PATH --public PATH        — пара ключей Ed25519
  build --version 0.1.1 [--channel beta] [--changelog FILE] [--min-supported 0.1.0]
        [--tag-suffix ""]                    — манифест manifest.json + manifest.sig
                                               (дайджесты через docker inspect по тегам
                                               erp-{api,worker,beat,web}<suffix>)
  verify MANIFEST [SIG]                      — проверка подписи публичным ключом из репо
  gen-test-manifest --version 0.1.1 [...]    — дев-манифест для file:// отладки

Приватный ключ — RELEASE_KEY_PATH (env), вне репо. Подпись — Ed25519 поверх
каноничного JSON (общий модуль src/core/update/manifest.py).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.update.manifest import (  # noqa: E402
    ManifestError,
    generate_keypair,
    load_manifest,
    read_signature,
    sign_manifest,
    verify_manifest,
)

SERVICES = ("api", "worker", "beat", "web")


def image_digest(reference: str) -> str:
    """Идентификатор образа (для локально собранных — config-digest .Id)."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", reference],
        capture_output=True, text=True, check=True,
    )
    digest = result.stdout.strip()
    if not digest.startswith("sha256:"):
        raise SystemExit(f"no digest for image {reference}: {digest}")
    return digest


def build_manifest(args: argparse.Namespace) -> dict:
    changelog = args.changelog
    if args.changelog_file:
        changelog = Path(args.changelog_file).read_text(encoding="utf-8")
    images = {}
    for service in SERVICES:
        tag = f"erp-{service}{args.tag_suffix}"
        images[service] = {"repo": tag, "digest": image_digest(tag)}
    return {
        "version": args.version,
        "channel": args.channel,
        "changelog": changelog,
        "min_supported": args.min_supported,
        "images": images,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_release(manifest: dict, out_dir: Path, private_key: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    signature = sign_manifest(manifest, private_key)
    (out_dir / "manifest.sig").write_text(signature, encoding="utf-8")
    print(f"manifest: {manifest_path}")
    print(f"signature: {out_dir / 'manifest.sig'}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="release.py")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private", required=True)
    keygen.add_argument("--public", required=True)

    build = sub.add_parser("build")
    for flag, default in (("--channel", "stable"), ("--changelog", ""), ("--min-supported", "0.1.0")):
        build.add_argument(flag, default=default)
    build.add_argument("--changelog-file")
    build.add_argument("--version", required=True)
    build.add_argument("--tag-suffix", default="")
    build.add_argument("--out", default=str(ROOT / "deploy" / "release"))

    test_manifest = sub.add_parser("gen-test-manifest")
    test_manifest.add_argument("--version", required=True)
    test_manifest.add_argument("--channel", default="beta")
    test_manifest.add_argument("--changelog", default="Тестовая сборка для локальной отладки оркестратора.")
    test_manifest.add_argument("--min-supported", default="0.1.0")
    test_manifest.add_argument("--tag-suffix", default="")
    test_manifest.add_argument("--out", default=str(ROOT / "deploy"))

    verify = sub.add_parser("verify")
    verify.add_argument("manifest")
    verify.add_argument("signature", nargs="?")

    args = parser.parse_args()

    if args.command == "keygen":
        generate_keypair(args.private, args.public)
        print(f"private: {args.private} (держать вне репо!)")
        print(f"public:  {args.public}")
        return 0

    if args.command == "verify":
        manifest = load_manifest(args.manifest)
        # подпись рядом с манифестом: <manifest-файл>.sig
        signature = (
            read_signature(args.signature)
            if args.signature
            else read_signature(str(args.manifest) + ".sig")
        )
        try:
            verify_manifest(manifest, signature)
        except ManifestError as exc:
            print(f"FAIL: {exc}")
            return 1
        print("OK: подпись действительна")
        return 0

    private_key = os.environ.get("RELEASE_KEY_PATH", "")
    if not private_key:
        print("RELEASE_KEY_PATH не задан (путь к приватному ключу, вне репо)", file=sys.stderr)
        return 2

    if args.command == "build":
        manifest = build_manifest(args)
    else:  # gen-test-manifest
        manifest = build_manifest(args)

    write_release(manifest, Path(args.out), private_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
