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
READONLY_PASSWORD = "readonly1pass"


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


def _login_tokens(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()


def _ensure_user(client, admin_headers, email: str, password: str, role: str = "user") -> None:
    client.post("/api/v1/users", json={
        "email": email, "password": password, "role": role,
    }, headers=admin_headers)  # 409 на дубль между прогонами — не важно


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


# ---------- Отзыв сессий и смена пароля (security-p0 п.3) ----------

PWD_EMAIL = "pwd.test@erp.local"


def _current_password(client, candidates: list[str]) -> str:
    for password in candidates:
        response = client.post("/api/v1/auth/login",
                               json={"email": PWD_EMAIL, "password": password})
        if response.status_code == 200:
            return password
    raise AssertionError("ни один из паролей не подходит (тест не самовосстанавливается)")


def test_change_password_kills_all_sessions(client, admin_headers):
    _ensure_user(client, admin_headers, PWD_EMAIL, "OldPass123")
    old_password = _current_password(client, ["OldPass123", "FreshPass9"])
    new_password = "FreshPass9" if old_password == "OldPass123" else "OldPass123"

    tokens = _login_tokens(client, PWD_EMAIL, old_password)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # неверный старый — 403; слабый новый — 422 с правилами
    assert client.post("/api/v1/auth/change-password", json={
        "old_password": "wrong1pass", "new_password": new_password,
    }, headers=headers).status_code == 403
    weak = client.post("/api/v1/auth/change-password", json={
        "old_password": old_password, "new_password": "12345678",
    }, headers=headers)
    assert weak.status_code == 422 and "password" in weak.text

    # смена — 200
    assert client.post("/api/v1/auth/change-password", json={
        "old_password": old_password, "new_password": new_password,
    }, headers=headers).status_code == 200

    # все старые токены умерли: access и refresh
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 401

    # новый логин работает, аудит password.changed записан
    _login_tokens(client, PWD_EMAIL, new_password)
    log = client.get("/api/v1/events/log?action=password.changed&limit=10",
                     headers=admin_headers)
    assert log.status_code == 200
    assert any(row["action"] == "password.changed" for row in log.json())


def test_logout_revokes_refresh(client, admin_headers):
    _ensure_user(client, admin_headers, "logout.test@erp.local", "Logout1Pass")
    tokens = _login_tokens(client, "logout.test@erp.local", "Logout1Pass")

    response = client.post("/api/v1/auth/logout",
                           json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200

    # refresh тем же токеном отклоняется; повторный logout идемпотентен
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/logout",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 200
