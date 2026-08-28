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


# ---------- Оркестрация инструментов и лимит шагов (этап D) ----------

def test_extract_tool_call():
    from src.modules.ai_agent.chat import _extract_tool_call

    assert _extract_tool_call('{"tool": "get_rate", "args": {"currency": "USD", "date": "2026-08-01"}}') == {
        "tool": "get_rate", "args": {"currency": "USD", "date": "2026-08-01"}}
    # обычный текст и повреждённый JSON — не tool-call
    assert _extract_tool_call('За август поступило 100 руб.') is None
    assert _extract_tool_call('{"tool": broken') is None
    assert _extract_tool_call('{"другой": "json"}') is None


def test_tool_loop_and_step_limit(monkeypatch):
    from src.modules.ai_agent import chat as chat_mod

    scripted = '{"tool": "get_cashflow", "args": {"date_from": "2026-08-01", "date_to": "2026-08-31"}}'
    calls = []
    responses = iter([{"content": scripted}, {"content": "Итого 100 руб."}])

    def fake_chat(messages, scenario="d", scripted_content=None):
        calls.append(list(messages))
        return next(responses)

    def fake_tool(name, args):
        return {"closing_balance": "100.00"}

    monkeypatch.setattr(chat_mod, "chat", fake_chat)
    monkeypatch.setattr("src.modules.ai_agent.tools.run_tool", fake_tool, raising=False)
    import src.modules.ai_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "run_tool", fake_tool)

    messages = [{"role": "user", "content": "сколько пришло"}]
    answer, used = chat_mod.chat_with_tools(messages, "test")
    assert any(t["tool"] == "get_cashflow" and t["ok"] for t in used)
    assert "100" in answer
    # результат инструмента приходит экранированным блоком [ДАННЫЕ]
    assert any("[ДАННЫЕ: результат инструмента" in m["content"] for m in calls[-1])

    # зацикленный мок -> останов по лимиту
    monkeypatch.setattr(chat_mod, "chat", lambda m, scenario="d", scripted_content=None:
                        {"content": scripted_content})
    answer2, used2 = chat_mod.chat_with_tools(messages, "test", scripted_content=scripted)
    assert len(used2) == 5
    assert "лимит" in answer2.lower()


def test_tools_whitelist_rejects_unknown():
    from src.modules.ai_agent.tools import run_tool

    result = run_tool("delete_everything", {})
    assert "unknown tool" in result["error"]
