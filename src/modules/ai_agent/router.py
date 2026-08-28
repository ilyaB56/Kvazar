"""API модуля ai_agent: /api/v1/ai/... (этапы B-E)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import WriteUser
from src.db import get_db
from src.modules.ai_agent import models as m
from src.modules.ai_agent import rag

router = APIRouter(tags=["ai"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class DocumentOut(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    is_deleted: bool

    model_config = {"from_attributes": True}


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile = File(...), user: WriteUser = None,
                          db: Session = Depends(get_db)):
    """Загрузка txt/md/csv (лимит 5 МБ); ключи/секреты — 422 (ADR-006)."""
    if file.filename and not file.filename.lower().endswith(rag.ALLOWED_SUFFIXES):
        raise HTTPException(422, f"Allowed types: {list(rag.ALLOWED_SUFFIXES)}")
    if (warning := rag.check_restricted(file.filename or "")) is not None:
        raise HTTPException(422, warning)
    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(422, "File exceeds 5 MB limit")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("cp1251", errors="replace")
    return rag.index_document(db, name=file.filename or "untitled",
                              content=content, uploaded_by=user.id)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(user: WriteUser, db: Session = Depends(get_db)):
    return db.scalars(select(m.Document).where(m.Document.is_deleted.is_(False))
                      .order_by(m.Document.created_at.desc())).all()


@router.delete("/documents/{document_id}")
def remove_document(document_id: uuid.UUID, user: WriteUser,
                    db: Session = Depends(get_db)):
    document = db.get(m.Document, document_id)
    if document is None or document.is_deleted:
        raise HTTPException(404, "Document not found")
    rag.delete_document(db, document)
    return {"ok": True}


@router.get("/search")
def search_documents(q: str = Query(...), limit: int = Query(5, ge=1, le=20),
                     user: WriteUser = None):
    """Top-k чанков по косинусной близости: текст + имя документа."""
    return [
        {"chunk_id": row["chunk_id"] if "chunk_id" in row else row["id"],
         "text": row["text"], "document_id": str(row["document_id"]),
         "document_name": row["document_name"], "similarity": float(row["similarity"])}
        for row in rag.search(q, limit)
    ]


# ---------- Чат с контекстом (этап C) ----------

class ChatIn(BaseModel):
    session_id: uuid.UUID | None = None
    message: str


class SourceOut(BaseModel):
    document_id: str
    document_name: str
    text: str


class ChatOut(BaseModel):
    session_id: str
    answer: str
    sources: list[SourceOut]


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/chat", response_model=ChatOut)
def ai_chat(body: ChatIn, user: WriteUser):
    from src.modules.ai_agent.chat import chat_reply

    try:
        return chat_reply(session_id=body.session_id, message=body.message,
                          user_id=user.id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(user: WriteUser, db: Session = Depends(get_db)):
    return db.scalars(select(m.ChatSession).where(m.ChatSession.user_id == user.id)
                      .order_by(m.ChatSession.created_at.desc())).all()


def _own_session(db, session_id: uuid.UUID, user) -> m.ChatSession:
    session = db.get(m.ChatSession, session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    is_admin = hasattr(user, "role") and user.role == "admin"
    if session.user_id != user.id and not is_admin:
        raise HTTPException(403, "Session belongs to another user")
    return session


@router.get("/sessions/{session_id}")
def get_session_messages(session_id: uuid.UUID, user: WriteUser,
                         db: Session = Depends(get_db)):
    session = _own_session(db, session_id, user)
    rows = db.scalars(select(m.ChatMessage).where(m.ChatMessage.session_id == session.id)
                      .order_by(m.ChatMessage.id)).all()
    return [{"id": row.id, "role": row.role, "content": row.content,
             "meta": row.meta, "created_at": row.created_at.isoformat()} for row in rows]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: uuid.UUID, user: WriteUser,
                   db: Session = Depends(get_db)):
    session = _own_session(db, session_id, user)
    db.query(m.ChatMessage).filter(m.ChatMessage.session_id == session.id).delete()
    db.delete(session)
    db.commit()
    return {"ok": True}


# ---------- Предложения и настройки (этап E) ----------

class ProposalOut(BaseModel):
    id: uuid.UUID
    action_type: str
    payload: dict
    reason: str
    status: str
    result: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class SettingsOut(BaseModel):
    autopapply: bool


@router.get("/proposals", response_model=list[ProposalOut])
def list_proposals(status: str | None = None, user: WriteUser = None,
                   db: Session = Depends(get_db)):
    query = select(m.Proposal).order_by(m.Proposal.created_at.desc()).limit(100)
    if status:
        query = query.where(m.Proposal.status == status)
    return db.scalars(query).all()


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalOut)
def approve_proposal(proposal_id: uuid.UUID, user: WriteUser):
    from src.modules.ai_agent.proposals import apply_proposal

    try:
        return apply_proposal(proposal_id, decided_by=user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
def reject_proposal_endpoint(proposal_id: uuid.UUID, user: WriteUser):
    from src.modules.ai_agent.proposals import reject_proposal

    try:
        return reject_proposal(proposal_id, decided_by=user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/settings", response_model=SettingsOut)
def get_settings_endpoint(user: WriteUser, db: Session = Depends(get_db)):
    from src.modules.ai_agent.proposals import get_autopapply

    return {"autopapply": get_autopapply(db, user.id)}


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: SettingsOut, user: WriteUser, db: Session = Depends(get_db)):
    """Автоприменение — только для ролей user/admin (readonly → 403)."""
    from src.modules.ai_agent.proposals import set_autopapply

    if getattr(user, "role", "user") == "readonly":
        raise HTTPException(403, "autopapply is not available for readonly")
    set_autopapply(db, user.id, body.autopapply)
    db.commit()
    return {"autopapply": body.autopapply}
