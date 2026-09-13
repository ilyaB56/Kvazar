"""RAG: загрузка документов, чанкинг, эмбеддинги, поиск (этап B).

Чанкинг ~1000 символов с перекрытием 15%; CSV индексируется построчно
с заголовком; эмбеддинги — коннектор (AI_EMBED_MODEL). Restricted-фильтр:
ключи/секреты (ADR-006 п.2) — 422.
"""

from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import text as sql_text

from src.db import SessionLocal
from src.modules.ai_agent import models as m
from src.modules.ai_agent.llm import embed

CHUNK_SIZE = 1000
OVERLAP = 0.15
RESTRICTED_SUFFIXES = (".pem", ".key", ".env")
ALLOWED_SUFFIXES = (".txt", ".md", ".csv")


def check_restricted(filename: str) -> str | None:
    """Имя похоже на ключ/секрет — предупреждение для 422 (ADR-006 п.2)."""
    lowered = filename.lower()
    for suffix in RESTRICTED_SUFFIXES:
        if lowered.endswith(suffix) or lowered.split("/")[-1] == suffix:
            return (f"Файл «{filename}» выглядит как ключ/секрет "
                    f"({suffix}): загрузка запрещена (ADR-006)")
    return None


def chunk_text(content: str, size: int = CHUNK_SIZE) -> list[str]:
    """Чанки ~size символов, перекрытие 15%; чистая функция."""
    overlap = int(size * OVERLAP)
    chunks: list[str] = []
    start = 0
    while start < len(content):
        piece = content[start:start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(content):
            break
        start += size - overlap
    return chunks or ([content.strip()] if content.strip() else [])


def chunks_for_file(filename: str, content: str) -> list[str]:
    """Чанки файла: CSV — построчно с заголовком, иначе скользящее окно."""
    if filename.lower().endswith(".csv"):
        rows = list(csv.reader(io.StringIO(content)))
        if not rows:
            return []
        header = ",".join(rows[0])
        return [f"{header}\n{','.join(row)}" for row in rows[1:] if row]
    return chunk_text(content)


def index_document(db, *, name: str, content: str, uploaded_by: uuid.UUID,
                    company_id: uuid.UUID | None = None) -> m.Document:
    """Создать документ + чанки с эмбеддингами (одной транзакцией вызова)."""
    document = m.Document(name=name, source_type="csv" if name.lower().endswith(".csv") else "file",
                          uploaded_by=uploaded_by, company_id=company_id)
    db.add(document)
    db.flush()
    for index, piece in enumerate(chunks_for_file(name, content)):
        db.add(m.Chunk(document_id=document.id, chunk_index=index,
                       text=piece, embedding=embed(piece)))
    db.commit()
    return document


def delete_document(db, document: m.Document) -> None:
    """Мягкое удаление документа + физическая чистка чанков."""
    db.query(m.Chunk).filter(m.Chunk.document_id == document.id).delete()
    document.is_deleted = True
    db.commit()


def search(q: str, limit: int = 5,
            company_id: uuid.UUID | None = None) -> list[dict]:
    """Top-k чанков по косинусной близости (<=> — дистанция, меньше = ближе).
    RAG — в рамках своей организации (chunks фильтруются join по documents)."""
    if not q.strip():
        return []
    query_vector = embed(q)
    vector_literal = "[" + ",".join(f"{v:.6f}" for v in query_vector) + "]"
    db = SessionLocal()
    try:
        company_filter = ("AND d.company_id = CAST(:company_id AS uuid)"
                          if company_id is not None else "")
        rows = db.execute(sql_text(f"""
            SELECT c.id, c.text, c.document_id, d.name AS document_name,
                   1 - (c.embedding <=> CAST(:qv AS vector)) AS similarity
            FROM {m.SCHEMA}.chunks c
            JOIN {m.SCHEMA}.documents d ON d.id = c.document_id
            WHERE d.is_deleted = false {company_filter}
            ORDER BY c.embedding <=> CAST(:qv AS vector)
            LIMIT :limit
        """), {"qv": vector_literal, "limit": limit,
               **({"company_id": str(company_id)} if company_id is not None else {})}).mappings().all()
        return [dict(row) for row in rows]
    finally:
        db.close()
