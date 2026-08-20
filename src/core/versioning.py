"""Журнал версий записей — единый хелпер ядра для всех модулей.

Версии пишет сервисный слой модулей (не триггеры): единая точка в коде,
проще тестировать. История доступна через API модулей (GET /history/...).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.core.models import RecordVersion


def record_version(
    db: Session,
    entity_type: str,
    entity_id: str,
    changed_by: uuid.UUID | None,
    diff: dict[str, dict[str, Any]] | None = None,
    reason: str | None = None,
) -> None:
    """Записать версию изменения сущности в erp_core.record_versions.

    diff — {поле: {"old": ..., "new": ...}}; значения должны быть
    JSON-сериализуемы (Decimal/дату приводите к строке заранее).
    """
    db.add(RecordVersion(
        entity_type=entity_type,
        entity_id=str(entity_id),
        changed_by=changed_by,
        diff=diff or {},
        reason=reason,
    ))
