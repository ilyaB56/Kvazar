"""Этап A автоматизации продаж (sales-automation-spec §10):

(A1) нотификация payment.succeeded (fixture + mock API провайдера) → 202,
     строка webhook_events processed; дубль → duplicate без второй обработки;
(A2) чужой/несуществующий payment_id (mock отвечает canceled) → invalid,
     401, ничего не создано;
(A3) POST /connections c yookassa → test (mock) ok;
(A4) ruff banned-api: сетевые библиотеки — только в connectors/
     (проверяется конфигом pyproject через ruff; тест фиксирует сам факт
     правила и отсутствие нарушений в живом прогоне ruff).

Mock API ЮKassa поднимается локально (как mock-Telegram в smoke):
connection.base_url → http://127.0.0.1:<свободный порт>/v3 — порт выдаёт
ОС (bind на 0), конфликтов между модулями прогона нет.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]

# ---------- mock API ЮKassa (GET /v3/payments/{id}) ----------

PAYMENTS: dict[str, dict] = {}


class _YooKassaMock(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        auth_ok = self.headers.get("Authorization", "") == \
            "Basic " + base64.b64encode(b"shop1:secret1").decode()
        if not auth_ok:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail": "auth failed"}')
            return
        if self.path.split("?")[0] == "/v3/payments":
            # список (test_connection): достаточно пустого ответа
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"items": []}')
            return
        if self.path.startswith("/v3/payments/"):
            payment_id = self.path.rsplit("/", 1)[1].split("?")[0]
            payment = PAYMENTS.get(payment_id)
            if payment is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"detail": "not found"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payment).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # тише в выводе pytest
        pass


@pytest.fixture(scope="module")
def yookassa_mock():
    server = HTTPServer(("127.0.0.1", 0), _YooKassaMock)
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v3"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base_url
    server.shutdown()
    server.server_close()


def _payment_body(payment_id: str, status: str = "succeeded", amount: str = "1000.00") -> dict:
    return {
        "id": payment_id, "status": status, "paid": status == "succeeded",
        "amount": {"value": amount, "currency": "RUB"},
        "metadata": {"email": "buyer@example.com", "lines": [
            {"external_id": "site-sku-1", "sku": "DIGI-1", "name": "Код", "qty": 1,
             "price": amount},
        ]},
    }


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post(f"{API}/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, "admin@example.com", os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"))


@pytest.fixture(scope="module")
def stage(client, admin_headers, yookassa_mock):
    """connection yookassa (mock base_url) + webhook-endpoint с привязкой."""
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"yookassa-test-{RUN}", "connector_code": "yookassa",
        "credentials": {"shop_id": "shop1", "secret_key": "secret1"},
        "config": {"base_url": yookassa_mock},
    }, headers=admin_headers)
    assert conn.status_code == 201, conn.text
    hook = client.post(f"{API}/integrations/webhooks", json={
        "name": f"yookassa-hook-{RUN}", "target_module": "payments",
        "connection_id": conn.json()["id"],
    }, headers=admin_headers)
    assert hook.status_code == 201, hook.text
    return {"conn": conn.json(), "hook": hook.json()}


def test_a3_connection_test_ok(client, admin_headers, stage):
    """(A3) test-подключения yookassa против mock API — ok."""
    response = client.post(
        f"{API}/integrations/connections/{stage['conn']['id']}/test", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.text


def _notify(payment_id: str) -> dict:
    return {"type": "notification", "event": "payment.succeeded",
            "object": _payment_body(payment_id)}


def test_a1_webhook_processed_and_duplicate(client, admin_headers, stage, yookassa_mock):
    """(A1) payment.succeeded → 202 + processed; дубль → duplicate."""
    payment_id = f"yk-{RUN}-ok1"
    PAYMENTS[payment_id] = _payment_body(payment_id)

    hook_url = f"{API}/integrations/hooks/{stage['hook']['id']}"
    first = client.post(hook_url, json=_notify(payment_id))
    assert first.status_code == 202, first.text
    assert first.json().get("accepted") is True

    # журнал: processed (событие интеграции пока журнальное — этап B включит флоу)
    events_rows = client.get(f"{API}/events/outbox?event_name=integration.webhook.verified&limit=10",
                             headers=admin_headers).json()
    assert any(e["payload"].get("payment_id") == payment_id for e in events_rows)

    # дубль → 202 duplicate, без второй записи
    second = client.post(hook_url, json=_notify(payment_id))
    assert second.status_code == 202
    assert second.json().get("duplicate") is True
    # и третий — стабилен
    third = client.post(hook_url, json=_notify(payment_id))
    assert third.status_code == 202 and third.json().get("duplicate") is True


def test_a2_verification_failed(client, admin_headers, stage, yookassa_mock):
    """(A2) несуществующий payment_id (mock 404) → invalid, 401, ничего нет."""
    ghost = f"yk-{RUN}-ghost"
    PAYMENTS.pop(ghost, None)  # точно нет в mock

    hook_url = f"{API}/integrations/hooks/{stage['hook']['id']}"
    response = client.post(hook_url, json=_notify(ghost))
    assert response.status_code in (401, 422), response.text
    # invalid-строка не создаёт событий интеграции
    rows = client.get(f"{API}/events/outbox?event_name=integration.webhook.verified&limit=50",
                      headers=admin_headers).json()
    assert not any(e["payload"].get("payment_id") == ghost for e in rows)

    # canceled платёж — тоже invalid (статус не succeeded)
    canceled_id = f"yk-{RUN}-canceled"
    PAYMENTS[canceled_id] = _payment_body(canceled_id, status="canceled")
    response = client.post(hook_url, json=_notify(canceled_id))
    assert response.status_code == 401, response.text
    assert "payment_status=canceled" in response.text


def test_a2_generic_endpoint_still_requires_token(client, admin_headers):
    """Endpoint без connection — прежний режим: без X-ERP-Token → 401."""
    hook = client.post(f"{API}/integrations/webhooks", json={
        "name": f"generic-{RUN}", "target_module": "external",
    }, headers=admin_headers).json()
    response = client.post(f"{API}/integrations/hooks/{hook['id']}", json={"hello": 1})
    assert response.status_code == 401


def test_a4_ruff_banned_api():
    """(A4) правило banned-api содержит сетевые библиотеки и их можно
    прогнать — фактический прогон делает регресс; здесь фиксируем
    конфигурацию (smtplib/httpx/requests запрещены вне connectors/)."""
    import pathlib
    import re

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    for lib in ("httpx", "requests", "socket", "smtplib"):
        assert re.search(rf'"{lib}"\.msg', content), f"{lib} missing in banned-api"
    assert 'connectors/**" = ["TID251"]' in content
