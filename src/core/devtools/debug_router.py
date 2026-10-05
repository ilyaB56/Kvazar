"""API отладчика (devtools-spec §8): /devtools/logs, /devtools/trace,
/devtools/diagnostics. Право devtools:ro; чтение не журналируется (О6)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.core.auth import User, require_module
from src.core.devtools import debugger
from src.core.devtools.debugger import DebuggerError
from src.db import get_db

router = APIRouter(tags=["devtools"])


def _require_devtools(user: User = Depends(require_module("devtools", "ro"))):
    return user


def _org_context(user: User) -> uuid.UUID | None:
    org = getattr(user, "token_org", None)
    return uuid.UUID(str(org)) if org else None


@router.get("/devtools/logs")
def logs(source: str,
         limit: int = Query(default=50, ge=1, le=10_000),
         offset: int = Query(default=0, ge=0),
         action: str | None = None, user_id: str | None = None,
         entity_type: str | None = None, q: str | None = None,
         host: str | None = None, status: str | None = None,
         event_name: str | None = None, processed: bool | None = None,
         job_id: str | None = None, step: str | None = None,
         error: str | None = None, event_type: str | None = None,
         user: User = Depends(_require_devtools),
         db: Session = Depends(get_db)):
    """Единая консоль журналов: source выбирает таблицу (§8.1); фильтры
    применяются по источнику, пагинация конвертом."""
    filters = {key: value for key, value in {
        "action": action, "user_id": user_id, "entity_type": entity_type,
        "q": q, "host": host, "status": status, "event_name": event_name,
        "processed": processed, "job_id": job_id, "step": step,
        "error": error, "event_type": event_type}.items()
        if value is not None}
    try:
        rows, total = debugger.fetch_logs(
            db, source=source, org_id=_org_context(user),
            filters=filters, limit=limit, offset=offset)
    except DebuggerError as exc:
        raise HTTPException(422, f"{exc.code}: {exc.detail}") from exc
    return {"items": rows, "total": total}


@router.get("/devtools/trace/{entity_type}/{entity_id}")
def trace(entity_type: str, entity_id: str,
          limit: int = Query(default=200, ge=1, le=200),
          user: User = Depends(_require_devtools),
          db: Session = Depends(get_db)):
    """Таймлайн сущности: версии + аудит + события шины (§8.2)."""
    return debugger.trace_entity(
        db, entity_type=entity_type, entity_id=entity_id,
        org_id=_org_context(user), limit=limit)


@router.get("/devtools/diagnostics")
def get_diagnostics(user: User = Depends(_require_devtools),
                    db: Session = Depends(get_db)):
    """Версии/здоровье/падения/коннекторы (§8.3, только чтение)."""
    return debugger.diagnostics(db, org_id=_org_context(user))
