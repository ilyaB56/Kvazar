"""Мультитенантность, этап A (multitenancy-spec §10.A): организации +
пользователи + вход (ядро).

Живой API-стиль + DB-хвост (CHECK-инвариант). Уникальные суффиксы за прогон;
teardown вычищает созданные организации/пользователей/сеансы.

- m1 вход супер-админа: токены без org, pl=true, organizations-реестр,
  has_2fa=false
- m2 GET /platform/orgs: реестр для pl; админ организации → 403
- m3 POST /platform/orgs: организация + админ + временный пароль (один раз);
  вход нового админа — полноценная пара с org его компании
- m4 select-org / leave-org: смена контекста, sid сохраняется
- m5 /users изолирован: админ org видит только своих; pl в контексте — свою;
  pl без org — всех
- m6 CHECK (company_id IS NULL) = is_platform_admin: юзер без организации
  и без pl не сохраняется (IntegrityError)
- m7 деактивация: вход пользователя → 403 organization_disabled;
  select-org деактивированной → 403; реактивация возвращает вход
- m8 select-org не-платформенным админом → 403
- m9 platform reset-password: временный пароль, сеансы убиты, вход по новому

Запуск: docker compose exec api pytest tests/test_multitenancy_a.py
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


def _claims(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    """Уборка: созданные за прогон организации/пользователи/сеансы."""
    t0 = datetime.now(timezone.utc)
    yield
    try:
        from sqlalchemy import text

        from src.db import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text(
                "DELETE FROM erp_core.auth_sessions WHERE created_at >= :t0"
                " AND (user_agent LIKE 'python-httpx/%' OR user_agent = 'mt-test')"
            ).bindparams(t0=t0))
            db.execute(text(
                "DELETE FROM erp_core.users WHERE email LIKE :pat"
            ).bindparams(pat=f"%-{RUN}@mt.test"))
            db.execute(text(
                "DELETE FROM erp_core.companies WHERE name LIKE :pat"
            ).bindparams(pat="mt-%-" + RUN))
            db.commit()
        finally:
            db.close()
        # деактивируем тест-организации прогона: реестр платформы
        # не пухнет от мусора (данные остаются, org скрыта из активных)
        db2 = SessionLocal()
        try:
            db2.execute(text(
                "UPDATE erp_core.companies SET is_active = false"
                " WHERE name LIKE :pat"
            ).bindparams(pat="mt-%"))
            db2.commit()
        finally:
            db2.close()
    except Exception:  # noqa: BLE001 — вне контейнера БД нет
        pass


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=20,
                        headers={"User-Agent": "mt-test"})
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _platform(http: httpx.Client) -> dict:
    return _login(http, "admin@example.com",
                  os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"))


def test_m1_platform_admin_login(client):
    data = _platform(client)
    claims = _claims(data["access_token"])
    assert claims.get("pl") is True and not claims.get("org")
    assert data["has_2fa"] is False
    names = [o["name"] for o in data["organizations"]]
    assert "Основная" in names


def test_m2_platform_registry_scoped(client):
    platform = _platform(client)
    response = client.get(f"{API}/platform/orgs", headers=_auth(platform["access_token"]))
    assert response.status_code == 200, response.text
    orgs = response.json()
    assert any(o["name"] == "Основная" and o["users_count"] >= 1 for o in orgs)

    # админ организации (не pl) → 403: создаём через платформу
    created = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-isolated-{RUN}", "inn": "",
        "admin_email": f"mt-iso-admin-{RUN}@mt.test", "admin_full_name": "Iso",
    }, headers=_auth(platform["access_token"]))
    assert created.status_code == 201, created.text
    org_admin = _login(client, f"mt-iso-admin-{RUN}@mt.test",
                       created.json()["temp_password"])
    response = client.get(f"{API}/platform/orgs",
                          headers=_auth(org_admin["access_token"]))
    assert response.status_code == 403, response.text


def test_m3_create_org_with_admin(client):
    platform = _platform(client)
    email = f"mt-org-admin-{RUN}@mt.test"
    response = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-new-{RUN}", "inn": "7707083893",
        "admin_email": email, "admin_full_name": "Новый админ",
    }, headers=_auth(platform["access_token"]))
    assert response.status_code == 201, response.text
    body = response.json()
    temp = body["temp_password"]
    assert temp and body["admin"]["must_change_password"] is True

    # вход нового админа: полноценная пара, org = его компания, pl=false
    mine = _login(client, email, temp)
    claims = _claims(mine["access_token"])
    assert claims.get("org") == body["id"] and not claims.get("pl")

    # повторное создание с тем же email → 409
    response = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-dup-{RUN}", "admin_email": email,
    }, headers=_auth(platform["access_token"]))
    assert response.status_code == 409, response.text


def test_m4_select_and_leave_org(client):
    platform = _platform(client)
    org_id = next(o["id"] for o in platform["organizations"])

    response = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"], "company_id": org_id})
    assert response.status_code == 200, response.text
    pair = response.json()
    claims = _claims(pair["access_token"])
    assert claims.get("org") == org_id and claims.get("pl") is True
    assert claims.get("sid") == _claims(platform["access_token"]).get("sid")

    response = client.post(f"{API}/auth/leave-org", json={
        "refresh_token": pair["refresh_token"]})
    assert response.status_code == 200
    left = _claims(response.json()["access_token"])
    assert not left.get("org") and left.get("pl") is True

    # прежняя (платформенная) пара остаётся валидной — другой контекст, тот же сеанс
    response = client.get(f"{API}/auth/me", headers=_auth(platform["access_token"]))
    assert response.status_code == 200


def test_m5_users_scoped_by_org(client):
    platform = _platform(client)
    # свежая организация с одним админом
    created = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-users-{RUN}", "admin_email": f"mt-users-admin-{RUN}@mt.test",
    }, headers=_auth(platform["access_token"])).json()
    org_admin = _login(client, f"mt-users-admin-{RUN}@mt.test", created["temp_password"])

    # админ организации видит только своих (1), без чужих
    response = client.get(f"{API}/users", headers=_auth(org_admin["access_token"]))
    rows = response.json()
    assert response.status_code == 200 and len(rows) == 1
    assert rows[0]["email"] == f"mt-users-admin-{RUN}@mt.test"

    # pl без org видит всех; pl в контексте org — только её
    response = client.get(f"{API}/users", headers=_auth(platform["access_token"]))
    all_count = len(response.json())
    assert all_count > 1
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"],
        "company_id": created["id"]}).json()
    response = client.get(f"{API}/users", headers=_auth(pair["access_token"]))
    assert len(response.json()) == 1


def test_m6_check_constraint_user_needs_org(client):
    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO erp_core.users (id, email, password_hash, role, is_active,
                                        token_version, company_id, is_platform_admin)
            VALUES (gen_random_uuid(), :email, 'x', 'user', true, 0, NULL, false)
        """).bindparams(email=f"mt-check-{RUN}@mt.test"))
        db.commit()
        db.rollback()
        raise AssertionError("CHECK пропустил пользователя без организации")
    except AssertionError:
        db.rollback()
        raise
    except Exception:
        db.rollback()  # IntegrityError — инвариант работает
    finally:
        db.close()


def test_m7_deactivation_blocks_login(client):
    platform = _platform(client)
    created = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-deact-{RUN}", "admin_email": f"mt-deact-admin-{RUN}@mt.test",
    }, headers=_auth(platform["access_token"])).json()
    email = f"mt-deact-admin-{RUN}@mt.test"
    _login(client, email, created["temp_password"])  # вход работает

    # деактивация
    response = client.patch(f"{API}/platform/orgs/{created['id']}",
                            json={"is_active": False},
                            headers=_auth(platform["access_token"]))
    assert response.status_code == 200 and response.json()["is_active"] is False

    response = client.post(f"{API}/auth/login", json={
        "email": email, "password": created["temp_password"]})
    assert response.status_code == 403 and "organization_disabled" in response.text

    # select-org деактивированной → 403
    response = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"], "company_id": created["id"]})
    assert response.status_code == 403, response.text

    # реактивация возвращает вход
    client.patch(f"{API}/platform/orgs/{created['id']}", json={"is_active": True},
                 headers=_auth(platform["access_token"]))
    response = client.post(f"{API}/auth/login", json={
        "email": email, "password": created["temp_password"]})
    assert response.status_code == 200


def test_m8_select_org_forbidden_for_org_admin(client):
    platform = _platform(client)
    created = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-sel-{RUN}", "admin_email": f"mt-sel-admin-{RUN}@mt.test",
    }, headers=_auth(platform["access_token"])).json()
    org_admin = _login(client, f"mt-sel-admin-{RUN}@mt.test", created["temp_password"])
    main_id = next(o["id"] for o in platform["organizations"])

    response = client.post(f"{API}/auth/select-org", json={
        "refresh_token": org_admin["refresh_token"], "company_id": main_id})
    assert response.status_code == 403, response.text


def test_m9_platform_reset_password(client):
    platform = _platform(client)
    created = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-reset-{RUN}", "admin_email": f"mt-reset-admin-{RUN}@mt.test",
    }, headers=_auth(platform["access_token"])).json()
    email = f"mt-reset-admin-{RUN}@mt.test"
    old = _login(client, email, created["temp_password"])

    response = client.post(f"{API}/platform/users/{created['admin']['id']}/reset-password",
                           headers=_auth(platform["access_token"]))
    assert response.status_code == 200, response.text
    new_pwd = response.json()["temp_password"]
    assert new_pwd != created["temp_password"]

    # старые токены/сеанс умерли
    response = client.get(f"{API}/auth/me", headers=_auth(old["access_token"]))
    assert response.status_code == 401

    # вход по новому паролю
    response = client.post(f"{API}/auth/login", json={"email": email, "password": new_pwd})
    assert response.status_code == 200
    # must_change_password — после сброса платформой
    response = client.get(f"{API}/auth/me",
                          headers=_auth(response.json()["access_token"]))
    assert response.json()["must_change_password"] is True
