"""ai_agent: чанкинг, поиск, секрет-фильтр (unit, без БД — этап B)."""

from __future__ import annotations

from src.modules.ai_agent.rag import chunk_text, chunks_for_file, check_restricted


def test_chunking_size_and_overlap():
    text = "x" * 3000
    chunks = chunk_text(text, size=1000)
    assert all(len(c) <= 1000 for c in chunks)
    # перекрытие 15%: каждый следующий чанк начинается за 850 до конца предыдущего
    assert len(chunks) == 4  # 3000 символов / шаг 850
    # короткий текст — один чанк
    assert chunk_text("короткий") == ["короткий"]


def test_csv_rows_indexed_with_header():
    content = "date,amount,note\n2026-01-01,100,Продажа\n2026-01-02,200,Возврат"
    chunks = chunks_for_file("report.csv", content)
    assert len(chunks) == 2
    assert chunks[0].startswith("date,amount,note")
    assert "Продажа" in chunks[0] and "Возврат" not in chunks[0]


def test_restricted_files_rejected():
    assert check_restricted("server.pem") is not None
    assert check_restricted("tls.key") is not None
    assert check_restricted(".env") is not None
    assert check_restricted("/path/to/prod.env") is not None
    assert check_restricted("report.txt") is None
    assert check_restricted("notes.md") is None


def test_mock_embeddings_bag_of_words_semantics():
    from src.modules.integrations.connectors.llm import _hash_embed

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    cat = _hash_embed("кот собака")
    dog = _hash_embed("собака")
    unrelated = _hash_embed("бухгалтерия налог")
    assert cosine(dog, cat) > 0.0            # общее слово «собака»
    assert cosine(dog, cat) > cosine(dog, unrelated)
