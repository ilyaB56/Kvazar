"""Проверка наличия обновлений (этап C).

Инфраструктурное исключение ADR-001: только пакет src/core/update/ ходит
в сеть за манифестом (file:// для дев-отладки или http(s)). Манифест
обязательно проверяется по подписи Ed25519; непроверяемый — ошибка,
результат проверки — в erp_core.settings (update.available) + событие шины.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from sqlalchemy import select

from src import __version__
from src.config import get_settings
from src.core import events
from src.core.models import Setting
from src.core.update.manifest import ManifestError, verify_manifest
from src.db import SessionLocal

logger = logging.getLogger(__name__)


class UpdateCheckError(Exception):
    """Проверка не удалась (сеть/подпись/формат) — причина в сообщении."""


def parse_semver(version: str) -> tuple[int, int, int]:
    parts = version.strip().lstrip("v").split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Not a semver version: {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def is_newer(candidate: str, current: str) -> bool:
    return parse_semver(candidate) > parse_semver(current)


def satisfies_min(current: str, min_supported: str) -> bool:
    return parse_semver(current) >= parse_semver(min_supported)


def fetch_manifest(url: str) -> tuple[dict[str, Any], str | None]:
    """Скачать манифест и подпись (<url>.sig); file:// — локальные пути."""
    if url.startswith("file://"):
        path = Path(url[len("file://"):])
        if not path.exists():
            raise UpdateCheckError(f"Manifest file not found: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        signature = None
        sig_path = Path(str(path) + ".sig")
        if sig_path.exists():
            signature = sig_path.read_text(encoding="utf-8").strip() or None
        return manifest, signature
    try:
        with urlopen(Request(url), timeout=30) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        signature = None
        try:
            with urlopen(Request(url + ".sig"), timeout=30) as response:
                signature = response.read().decode("utf-8").strip() or None
        except OSError:
            pass
        return manifest, signature
    except OSError as exc:
        raise UpdateCheckError(f"Cannot fetch manifest {url}: {exc}") from exc


def check_update() -> dict[str, Any]:
    """Полный цикл проверки: манифест → подпись → версии → settings + событие."""
    manifest, signature = fetch_manifest(get_settings().update_manifest_url)
    try:
        verify_manifest(manifest, signature)
    except ManifestError as exc:
        raise UpdateCheckError(str(exc)) from exc

    available = is_newer(manifest["version"], __version__)
    info: dict[str, Any] = {
        "version": manifest["version"],
        "changelog": manifest.get("changelog", ""),
        "channel": manifest.get("channel", "stable"),
        "available": available,
        "checked_at": datetime.now(UTC).isoformat(),
    }

    db = SessionLocal()
    try:
        row = db.scalar(select(Setting).where(Setting.key == "update.available"))
        if row is None:
            db.add(Setting(key="update.available", value=info, value_type="json"))
        else:
            row.value = info
            row.value_type = "json"
        db.commit()
        if available:
            events.publish(db, "system.update.available", {
                "version": manifest["version"],
                "channel": manifest.get("channel", "stable"),
            })
            db.commit()
    finally:
        db.close()
    logger.info("update check: current=%s latest=%s available=%s",
                __version__, manifest["version"], available)
    return info
