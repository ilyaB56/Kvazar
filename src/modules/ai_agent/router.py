"""API модуля ai_agent: /api/v1/ai/... (этапы B-E)."""

from __future__ import annotations

import uuid

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
