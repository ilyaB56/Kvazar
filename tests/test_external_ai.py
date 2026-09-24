"""Внешний ИИ через интеграции (блок 1, ADR-006).

- коннектор external_ai: OpenAI-совместимый /chat/completions и Anthropic
  /v1/messages, Bearer/x-api-key, модель из config
- маршрутизация: активное подключение external_ai → чат идёт во внешний
  ИИ; ошибка/отсутствие — прежний провайдер (ollama/мок)
- function calling не меняется: TOOL_SCHEMAS уже в system prompt,
  ответ-JSON парсится существующим циклом chat_with_tools

Запуск: docker compose exec api pytest tests/test_external_ai.py
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration

REPLIES: list[dict] = []


class _OpenAICompatMock(BaseHTTPRequestHandler):
    """Мок внешнего ИИ: поочерёдно отдаёт REPLIES; пишет запросы в LOG."""

    def do_POST(self):  # noqa: NPT001/N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        LOG.append({"path": self.path, "auth": self.headers.get("Authorization", ""),
                    "x_api_key": self.headers.get("x-api-key", ""),
                    "body": body})
        reply = REPLIES.pop(0) if REPLIES else {
            "choices": [{"message": {"content": "внешний ответ"}}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *args):
        pass


LOG: list[dict] = []


@pytest.fixture(scope="module")
def mock_ai():
    server = HTTPServer(("127.0.0.1", 0), _OpenAICompatMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=60)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running")
    yield http
    http.close()


def _admin(client):
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"],
        "company_id": login["organizations"][0]["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


def test_connector_openai_style(mock_ai):
    """Прямой вызов коннектора:Bearer, модель, контент из choices."""
    from src.modules.integrations.connectors.builtin import registry

    REPLIES.append({"choices": [{"message": {"content": "привет из внешнего ИИ"}}]})
    conn = registry.build("external_ai",
                          {"base_url": mock_ai, "model": "glm-4.6", "style": "openai"},
                          {"api_key": "sk-test-123"})
    result = conn.push(payload={"messages": [{"role": "user", "content": "hi"}]})
    assert result.ok, result.error
    assert result.data["content"] == "привет из внешнего ИИ"
    request = LOG[-1]
    assert request["auth"] == "Bearer sk-test-123"
    assert request["body"]["model"] == "glm-4.6"
    assert request["body"]["messages"][0]["content"] == "hi"


def test_connector_anthropic_style(mock_ai):
    """Anthropic: x-api-key, system на верхнем уровне, контент из blocks."""
    from src.modules.integrations.connectors.builtin import registry

    REPLIES.append({"content": [{"type": "text", "text": "антропный ответ"}]})
    conn = registry.build("external_ai",
                          {"base_url": mock_ai, "model": "claude-x", "style": "anthropic"},
                          {"api_key": "ak-test"})
    result = conn.push(payload={"messages": [
        {"role": "system", "content": "ты ассистент"},
        {"role": "user", "content": "вопрос"}]})
    assert result.ok, result.error
    assert result.data["content"] == "антропный ответ"
    request = LOG[-1]
    assert request["x_api_key"] == "ak-test"
    assert request["path"].endswith("/v1/messages")
    assert request["body"]["system"] == "ты ассистент"
    assert request["body"]["messages"] == [{"role": "user", "content": "вопрос"}]


def test_patch_connection_toggle_and_rename(client):
    """PATCH /integrations/connections/{id}: is_active + name (удаления
    нет — деактивация сохраняет историю)."""
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"patch-me-{RUN}", "connector_code": "http_rest",
        "credentials": {}, "config": {"base_url": "http://api:8000"},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    cid = conn.json()["id"]
    try:
        off = client.patch(f"{API}/integrations/connections/{cid}",
                           json={"is_active": False, "name": f"переименован-{RUN}"},
                           headers=headers)
        assert off.status_code == 200, off.text
        assert off.json()["is_active"] is False
        assert off.json()["name"] == f"переименован-{RUN}"
        on = client.patch(f"{API}/integrations/connections/{cid}",
                          json={"is_active": True}, headers=headers)
        assert on.status_code == 200 and on.json()["is_active"] is True
        missing = client.patch(f"{API}/integrations/connections/"
                               "00000000-0000-0000-0000-000000000001",
                               json={"is_active": True}, headers=headers)
        assert missing.status_code == 404
    finally:
        _deactivate(cid)


def _deactivate(conn_id: str):
    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("UPDATE integrations.connections SET is_active = false"
                        " WHERE id = :i").bindparams(i=conn_id))
        db.commit()
    finally:
        db.close()


def test_routing_via_connection(client, mock_ai):
    """Активное подключение external_ai → /ai/chat отвечает внешний ИИ."""
    headers = _admin(client)
    REPLIES.append({"choices": [{"message": {"content": f"внешний-ответ-{RUN}"}}]})
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ext-ai-{RUN}", "connector_code": "external_ai",
        "credentials": {"api_key": "sk-live"},
        "config": {"base_url": mock_ai, "model": "glm-4.6", "style": "openai"},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    try:
        reply = client.post(f"{API}/ai/chat", json={"message": "привет"},
                            headers=headers)
        assert reply.status_code == 200, reply.text
        assert f"внешний-ответ-{RUN}" in json.dumps(reply.json(), ensure_ascii=False)
        # запрос ушёл во внешний ИИ с Bearer из credentials
        assert any(r["auth"] == "Bearer sk-live" for r in LOG)
    finally:
        # деактивация → маршрутизация возвращается к ollama/моку
        _deactivate(conn.json()["id"])


def test_fallback_on_external_error(client):
    """Мёртвый внешний ИИ → фолбэк: chat() не падает, идёт на основной
    провайдер (соединение деактивировано в конце)."""
    from src.modules.ai_agent.llm import chat as llm_chat

    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ext-ai-dead-{RUN}", "connector_code": "external_ai",
        "credentials": {"api_key": "sk-dead"},
        "config": {"base_url": "http://127.0.0.1:9", "model": "glm-4.6"},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    try:
        try:
            result = llm_chat([{"role": "user", "content": "ping"}])
            assert "content" in result  # основной провайдер ответил
        except RuntimeError as exc:
            # основной провайдер (напр., ollama без профиля ai) может быть
            # недоступен — важно, что это ЕГО ошибка, а не внешнего ИИ
            assert "127.0.0.1:9" not in str(exc) and "external" not in str(exc).lower()
    finally:
        _deactivate(conn.json()["id"])
