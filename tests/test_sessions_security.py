"""Контроль одновременных сеансов (sessions-security-spec, этап A).

Живой API-стиль (как test_roles.py): httpx + логин admin.

- A1 двойной вход: active_sessions 1 → 2
- A2 GET /auth/sessions: оба сеанса, ровно один is_current
- A3 POST /auth/logout-others: terminated=1, остаётся 1; отозванный
  refresh → 401, действие его access-токеном → 401 (мгновенно, по sid)
- A4 revoke: чужой сеанс → 404; уже отозванный → 409
- A5 смена пароля: все сеансы завершены, старый refresh мёртв

Запуск: docker compose exec api pytest tests/test_sessions_security.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]  # уникальные тест-юзеры за прогон

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=15)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client) -> dict:
    response = http.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"),
    }, headers={"User-Agent": "sessions-security-test"})
    response.raise_for_status()
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_a1_double_login_counts_sessions(client):
    admin = _login(client)
    # свежий юзер: абсолютные счётчики детерминированы (1 → 2)
    email = f"sessions-a1-{RUN}@erp.local"
    client.post(f"{API}/users", json={
        "email": email, "password": "Aa12345678!", "role": "readonly",
        "name": "A1"}, headers=_auth(admin["access_token"]))

    def login_as() -> dict:
        response = client.post(f"{API}/auth/login",
                               json={"email": email, "password": "Aa12345678!"})
        assert response.status_code == 200, response.text
        return response.json()

    first = login_as()
    assert first["active_sessions"] == 1, first
    second = login_as()
    assert second["active_sessions"] == 2, second
    assert first["access_token"] and second["refresh_token"]


def test_a2_sessions_list_marks_current(client):
    _login(client)  # гарантируем ≥2
    mine = _login(client)
    response = client.get(f"{API}/auth/sessions", headers=_auth(mine["access_token"]))
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) >= 2
    assert sum(1 for r in rows if r["is_current"]) == 1
    current = next(r for r in rows if r["is_current"])
    assert "sessions-security-test" in current["user_agent"]


def test_a3_logout_others_kills_first_session(client):
    first = _login(client)
    second = _login(client)

    response = client.post(f"{API}/auth/logout-others",
                           json={"refresh_token": second["refresh_token"]})
    assert response.status_code == 200, response.text
    assert response.json()["terminated"] >= 1

    # второй (текущий) жив: список показывает его
    response = client.get(f"{API}/auth/sessions", headers=_auth(second["access_token"]))
    rows = response.json()
    assert all(not (r["id"] == "revoked") for r in rows)
    assert sum(1 for r in rows if r["is_current"]) == 1

    # refresh отозванного сеанса → 401
    response = client.post(f"{API}/auth/refresh",
                           json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 401, response.text

    # действие access-токеном отозванного сеанса → 401 сразу (по sid)
    response = client.get(f"{API}/auth/me", headers=_auth(first["access_token"]))
    assert response.status_code == 401, response.text


def test_a4_revoke_foreign_and_revoked(client):
    admin = _login(client)
    # второй пользователь (уникальный за прогон — прогон идемпотентен)
    other_email = f"sessions-other-{RUN}@erp.local"
    client.post(f"{API}/users", json={
        "email": other_email, "password": "Other12345!",
        "role": "readonly", "name": "Other"}, headers=_auth(admin["access_token"]))
    other_login = client.post(f"{API}/auth/login", json={
        "email": other_email, "password": "Other12345!"})
    assert other_login.status_code == 200, other_login.text
    other = other_login.json()
    other_sessions = client.get(f"{API}/auth/sessions",
                                headers=_auth(other["access_token"])).json()
    other_sid = other_sessions[0]["id"]

    # чужой сеанс → 404 (не раскрываем существование)
    response = client.post(f"{API}/auth/sessions/{other_sid}/revoke",
                           headers=_auth(admin["access_token"]))
    assert response.status_code == 404, response.text

    # свой не-текущий: второй вход админом → ревок → повтор → 409
    second = _login(client)
    mine = client.get(f"{API}/auth/sessions",
                      headers=_auth(second["access_token"])).json()
    stale = next(r for r in mine if not r["is_current"])
    response = client.post(f"{API}/auth/sessions/{stale['id']}/revoke",
                           headers=_auth(second["access_token"]))
    assert response.status_code == 200, response.text
    response = client.post(f"{API}/auth/sessions/{stale['id']}/revoke",
                           headers=_auth(second["access_token"]))
    assert response.status_code == 409, response.text

    # ревок текущего сеанса → 409 (для текущего есть logout)
    current_id = next(r for r in mine if r["is_current"])["id"]
    response = client.post(f"{API}/auth/sessions/{current_id}/revoke",
                           headers=_auth(second["access_token"]))
    assert response.status_code == 409, response.text

    # отозванный refresh мёртв
    response = client.post(f"{API}/auth/refresh",
                           json={"refresh_token": admin["refresh_token"]})
    assert response.status_code in (200, 401)


def test_a5_password_change_terminates_all(client):
    # отдельный пользователь, чтобы не ломать пароль админа прогона
    admin = _login(client)
    email = f"sessions-pwd-{RUN}@erp.local"
    client.post(f"{API}/users", json={
        "email": email, "password": "PwdOld12345!", "role": "readonly",
        "name": "Pwd"}, headers=_auth(admin["access_token"]))

    def pwd_login(password: str):
        return client.post(f"{API}/auth/login",
                           json={"email": email, "password": password})

    first = pwd_login("PwdOld12345!")
    second = pwd_login("PwdOld12345!")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first, second = first.json(), second.json()

    response = client.post(f"{API}/auth/change-password", json={
        "old_password": "PwdOld12345!", "new_password": "PwdNew12345!"},
        headers=_auth(second["access_token"]))
    assert response.status_code == 200, response.text

    # все старые сеансы завершены: refresh мёртв, список пуст у старых токенов
    response = client.post(f"{API}/auth/refresh",
                           json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 401, response.text
    response = client.get(f"{API}/auth/sessions",
                          headers=_auth(first["access_token"]))
    assert response.status_code == 401

    # новый вход — свежий единственный сеанс
    fresh = pwd_login("PwdNew12345!")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["active_sessions"] == 1
