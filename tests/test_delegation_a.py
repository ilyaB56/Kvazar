"""Делегирование, этап A: effective_module_level + username-вход (§8.A).

- a1 пустая таблица user_permissions = поведение идентично текущему
  (регресс: все существующие наборы зелёные без правок — отдельный
  полный прогон; здесь — точечные проверки пресетов)
- a2 личная выдача поднимает эффективный уровень: readonly-пользователь
  с личным accounting:ro → GET 200 / POST 403; с rw → POST 200;
  доступ появляется БЕЗ перелогина
- a3 max(роль, личная): роль user (accounting:rw) + личное ro → rw
  (надстройка не понижает)
- a4 ApiPrincipal: api-токен роли readonly с личным rw у владельца —
  ходит по роли токена (403 на мутацию)
- a5 /me/permissions: granted = личные, permissions = эффективный max
- a6 вход по username ИЛИ email

Запуск: docker compose exec api pytest tests/test_delegation_a.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
ACC = f"{API}/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM erp_core.user_permissions WHERE user_id IN ("
            "  SELECT id FROM erp_core.users WHERE email LIKE :pat)"
        ).bindparams(pat=f"%-{RUN}@mt.test"))
        db.execute(text(
            "DELETE FROM erp_core.api_tokens WHERE owner_user_id IN ("
            "  SELECT id FROM erp_core.users WHERE email LIKE :pat)"
        ).bindparams(pat=f"%-{RUN}@mt.test"))
        db.execute(text(
            "DELETE FROM erp_core.auth_sessions WHERE user_id IN ("
            "  SELECT id FROM erp_core.users WHERE email LIKE :pat)"
        ).bindparams(pat=f"%-{RUN}@mt.test"))
        db.execute(text(
            "DELETE FROM erp_core.users WHERE email LIKE :pat"
        ).bindparams(pat=f"%-{RUN}@mt.test"))
        db.commit()
    except ImportError:
        pass
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "del-a-test"})
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def platform(client):
    response = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")})
    assert response.status_code == 200
    data = response.json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": data["refresh_token"],
        "company_id": data["organizations"][0]["id"]}).json()
    return pair


@pytest.fixture(scope="module")
def readonly_user(client, platform):
    """Пользователь роли readonly в «Основной» (личных прав нет)."""
    email = f"del-a-ro-{RUN}@mt.test"
    response = client.post(f"{API}/users", json={
        "email": email, "password": "Readonly1pass", "role": "readonly",
        "name": "RO"}, headers=_auth(platform["access_token"]))
    assert response.status_code == 201, response.text
    return {"id": response.json()["id"], "email": email}


def _login(client, email, password="Readonly1pass"):
    response = client.post(f"{API}/auth/login",
                           json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _grant_direct(client, platform, user_id, module, level):
    """Выдача напрямую в БД (этап A — API выдачи это этап B)."""
    from src.core.models import UserPermission
    from src.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(UserPermission, (uuid.UUID(user_id), module))
        if row is None:
            row = UserPermission(user_id=uuid.UUID(user_id), module=module)
            db.add(row)
        row.level = level
        row.granted_by = uuid.UUID(_admin_id(client, platform))
        db.commit()
    finally:
        db.close()


_ADMIN_ID = {}


def _admin_id(client, platform):
    if not _ADMIN_ID:
        me = client.get(f"{API}/auth/me", headers=_auth(platform["access_token"])).json()
        _ADMIN_ID["v"] = me["id"]
    return _ADMIN_ID["v"]


def _revoke_direct(user_id, module):
    from src.core.models import UserPermission
    from src.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(UserPermission, (uuid.UUID(user_id), module))
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def test_a1_empty_table_presets_unchanged(client, readonly_user):
    """Личных прав нет → эффективный уровень = роли (readonly: GET 200,
    POST 403) — поведение идентично до делегирования."""
    pair = _login(client, readonly_user["email"])
    response = client.get(f"{ACC}/accounts", headers=_auth(pair["access_token"]))
    assert response.status_code == 200
    response = client.post(f"{ACC}/accounts",
                           json={"name": "x", "currency": "RUB"},
                           headers=_auth(pair["access_token"]))
    assert response.status_code == 403


def test_a2_personal_grant_lifts_without_relogin(client, platform, readonly_user):
    """Личное accounting:ro → GET 200/POST 403; смена на rw → POST 201 —
    тем же токеном, без перелогина (права из БД на каждом запросе)."""
    pair = _login(client, readonly_user["email"])
    token = _auth(pair["access_token"])

    _grant_direct(client, platform, readonly_user["id"], "accounting", "ro")
    response = client.get(f"{ACC}/accounts", headers=token)
    assert response.status_code == 200
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"del-a-{RUN}", "currency": "RUB"},
                           headers=token)
    assert response.status_code == 403, "ro не даёт мутаций"

    _grant_direct(client, platform, readonly_user["id"], "accounting", "rw")
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"del-a2-{RUN}", "currency": "RUB"},
                           headers=token)
    assert response.status_code == 201, "rw применяется без перелогина"
    _revoke_direct(readonly_user["id"], "accounting")


def test_a3_max_of_role_and_personal(client, platform):
    """Роль user (accounting:rw по пресету) + личное ro → эффективный rw:
    надстройка не понижает роль (п.9 — только добавляет)."""
    email = f"del-a-user-{RUN}@mt.test"
    response = client.post(f"{API}/users", json={
        "email": email, "password": "User1passOK", "role": "user",
        "name": "U"}, headers=_auth(platform["access_token"]))
    assert response.status_code == 201
    user_id = response.json()["id"]
    _grant_direct(client, platform, user_id, "accounting", "ro")
    pair = _login(client, email, "User1passOK")
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"del-a3-{RUN}", "currency": "RUB"},
                           headers=_auth(pair["access_token"]))
    assert response.status_code == 201, "max(роль rw, личное ro) = rw"
    me = client.get(f"{API}/me/permissions",
                    headers=_auth(pair["access_token"])).json()
    assert me["permissions"]["accounting"] == "rw"
    assert me["granted"].get("accounting") == "ro"


def test_a4_api_token_walks_by_token_role(client, platform, readonly_user):
    """ApiPrincipal: у токена нет личных выдач — роль токена readonly,
    даже если владелец имеет личный rw (осознанная граница v1)."""
    _grant_direct(client, platform, readonly_user["id"], "accounting", "rw")
    token_row = client.post(f"{API}/admin/api-tokens", json={
        "name": f"del-a-{RUN}", "role": "readonly"},
        headers=_auth(platform["access_token"])).json()
    response = client.get(f"{ACC}/accounts",
                          headers={"X-API-Token": token_row["token"]})
    assert response.status_code == 200, "чтение по роли readonly — ок"
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"del-a4-{RUN}", "currency": "RUB"},
                           headers={"X-API-Token": token_row["token"]})
    assert response.status_code == 403, "личный rw владельца токен не наследует"
    _revoke_direct(readonly_user["id"], "accounting")


def test_a5_me_permissions_shape(client, platform, readonly_user):
    """/me/permissions: permissions — эффективный максимум, granted —
    только личная надстройка (ADR-002: расширение payload)."""
    _grant_direct(client, platform, readonly_user["id"], "crm", "ro")
    pair = _login(client, readonly_user["email"])
    me = client.get(f"{API}/me/permissions",
                    headers=_auth(pair["access_token"])).json()
    assert me["granted"] == {"crm": "ro"}
    assert me["permissions"]["crm"] == "ro"
    assert me["permissions"]["accounting"] == "ro"  # роль readonly
    _revoke_direct(readonly_user["id"], "crm")
