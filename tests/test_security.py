"""Security-plan P0-1: readonly-роль — только чтение, мутации запрещены.

Интеграционные тесты против запущенного API (docker compose up).
Readonly-пользователь создаётся один раз и переиспользуется между прогонами.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration

READONLY_EMAIL = "readonly.test@erp.local"
READONLY_PASSWORD = "readonly-test-password"


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=10)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, "admin@example.com", os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"))


@pytest.fixture(scope="module")
def readonly_headers(client, admin_headers):
    # создаём один раз; на повторных прогонах дубль даёт 409 — игнорируем
    client.post("/api/v1/users", json={
        "email": READONLY_EMAIL, "password": READONLY_PASSWORD, "role": "readonly",
    }, headers=admin_headers)
    return _login(client, READONLY_EMAIL, READONLY_PASSWORD)


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v1/companies", {"name": "x"}),
    ("POST", "/api/v1/contacts", {"full_name": "x"}),
    ("POST", "/api/v1/integrations/webhooks", {"name": "x"}),
    ("POST", "/api/v1/integrations/sync-jobs",
     {"name": "x", "connection_id": "00000000-0000-0000-0000-000000000000"}),
    ("POST", "/api/v1/accounting/accounts", {"name": "x", "currency": "RUB"}),
    ("POST", "/api/v1/accounting/categories", {"name": "x", "kind": "income"}),
    ("POST", "/api/v1/accounting/counterparties", {"name": "x"}),
    ("POST", "/api/v1/accounting/rates", {"date": "2099-01-01", "currency": "USD", "rate": "1"}),
])
def test_readonly_cannot_write(client, readonly_headers, method, path, body):
    response = client.request(method, path, json=body, headers=readonly_headers)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_readonly_can_read(client, readonly_headers):
    for path in (
        "/api/v1/auth/me",
        "/api/v1/accounting/accounts",
        "/api/v1/accounting/periods",
        "/api/v1/integrations/connectors",
        "/api/v1/integrations/connections",
    ):
        assert client.get(path, headers=readonly_headers).status_code == 200, path


def test_readonly_blocked_from_admin_reads(client, readonly_headers):
    assert client.get("/api/v1/users", headers=readonly_headers).status_code == 403
    assert client.get("/api/v1/events/outbox", headers=readonly_headers).status_code == 403
