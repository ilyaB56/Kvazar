"""CLI проверки манифеста для оркестратора: exit 0 = подпись действительна.

Вызывается хостовым deploy/update.py через контейнер:
  docker compose run --rm --no-deps -v manifest.json:/tmp/manifest.json:ro \
    api python -m src.core.update.verify /tmp/manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.update.manifest import ManifestError, load_manifest, verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="src.core.update.verify")
    parser.add_argument("manifest", help="путь к manifest.json (подпись рядом: <manifest>.sig)")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    sig_path = Path(str(args.manifest) + ".sig")
    signature = None
    if sig_path.exists():
        signature = sig_path.read_text(encoding="utf-8").strip() or None
    try:
        verify_manifest(manifest, signature)
    except ManifestError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "version": manifest.get("version"),
        "min_supported": manifest.get("min_supported"),
        "channel": manifest.get("channel"),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
