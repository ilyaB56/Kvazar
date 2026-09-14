"""Мультитенантность C: 2FA TOTP для руководителей (multitenancy-spec §10.C).

- c1 обязательность: вход админа организации без 2FA → mfa_setup_required;
  сотрудник — сразу пара (второго шага нет)
- c2 полный цикл: setup → confirm (код по секрету) → backup_codes один раз →
  повторный вход → mfa_required → verify TOTP-кодом → полноценная пара
- c3 резервный код гасится: вход по резервному ок, повтор того же кода — 401
- c4 неверный код ×6 → 429 (тот же счётчик, что password-confirm)
- c5 сброс платформой → повторный вход запускает setup заново; сотруднику
  setup — 403
- c6 ruff banned-api: pyotp только в core/totp (проверяется конфигом)

Секрет для генерации кодов — pyotp из тестового процесса (вне src —
tests/ в per-file-ignores). Запуск: docker compose exec api pytest
tests/test_multitenancy_c.py (нужен pip install pyotp в контейнере).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


def _totp_now(secret: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).now()


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "mt-c-test"})
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
    return response.json()


_SEQ = [0]


def _new_org(client, platform, tag):
    _SEQ[0] += 1
    suffix = f"{tag}{_SEQ[0]}-{RUN}"
    response = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-C-{suffix}",
        "admin_email": f"mt-c-{suffix}@mt.test", "admin_full_name": "C",
    }, headers=_auth(platform["access_token"]))
    assert response.status_code == 201, response.text
    body = response.json()
    return body


def test_c1_admin_requires_setup_employee_skips(client, platform):
    body = _new_org(client, platform, "a")
    # админ организации без 2FA → обязательная настройка (О1)
    login = client.post(f"{API}/auth/login", json={
        "email": body["admin"]["email"], "password": body["temp_password"]})
    assert login.status_code == 200
    data = login.json()
    # по заданию этапа C: пара выдаётся, UI запускает мастер по флагу
    assert data["access_token"] and data["totp_setup_required"] is True

    # сотрудник той же организации — сразу пара (2FA не предлагается)
    org_ctx = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"],
        "company_id": body["id"]}).json()
    employee = client.post(f"{API}/users", json={
        "email": f"mt-c-emp-{RUN}@mt.test", "password": "Employee1pass",
        "role": "user", "name": "emp"}, headers=_auth(org_ctx["access_token"]))
    assert employee.status_code == 201, employee.text
    emp_login = client.post(f"{API}/auth/login", json={
        "email": f"mt-c-emp-{RUN}@mt.test", "password": "Employee1pass"})
    emp = emp_login.json()
    assert emp["access_token"] and not emp.get("mfa_required") \
        and not emp.get("mfa_setup_required")


def test_c2_full_cycle_and_login(client, platform):
    body = _new_org(client, platform, "b")
    email = body["admin"]["email"]
    password = body["temp_password"]

    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    access = login["access_token"]
    assert login["totp_setup_required"] is True

    # setup по access-токену (мастер UI)
    setup = client.post(f"{API}/auth/totp/setup", json={},
                        headers=_auth(access))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://")

    # confirm кодом по секрету → 2FA включена, бэкапы показаны один раз
    confirm = client.post(f"{API}/auth/totp/confirm", json={
        "code": _totp_now(secret)}, headers=_auth(access))
    assert confirm.status_code == 200, confirm.text
    result = confirm.json()
    codes = result["backup_codes"]
    assert len(codes) == 10 and all(len(c) == 4 + 1 + 4 for c in codes)  # XXXX-XXXX

    # повторный вход: mfa_required → verify TOTP-кодом → пара
    login2 = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    assert login2["mfa_required"] is True and not login2["access_token"]
    verify = client.post(f"{API}/auth/mfa/verify", json={
        "mfa_token": login2["mfa_token"], "code": _totp_now(secret)})
    assert verify.status_code == 200, verify.text
    pair = verify.json()
    assert pair["access_token"] and pair["has_2fa"] is True
    me = client.get(f"{API}/auth/me", headers=_auth(pair["access_token"]))
    assert me.status_code == 200

    return {"email": email, "password": password, "secret": secret,
            "backup": codes[0]}


def test_c3_c4_c5(client, platform):
    ctx = test_c2_full_cycle_and_login(client, platform)
    email, password = ctx["email"], ctx["password"]

    # c3: вход по резервному коду — ок; повтор того же кода — 401
    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    ok = client.post(f"{API}/auth/mfa/verify", json={
        "mfa_token": login["mfa_token"], "code": ctx["backup"]})
    assert ok.status_code == 200, ok.text
    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    reused = client.post(f"{API}/auth/mfa/verify", json={
        "mfa_token": login["mfa_token"], "code": ctx["backup"]})
    assert reused.status_code == 401

    # c4: неверные коды до 429 (счётчик общий с c3 — входил 1 неверный);
    # в пределах 6 попыток подряд должен наступить 429 с Retry-After
    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    statuses = []
    for i in range(6):
        response = client.post(f"{API}/auth/mfa/verify", json={
            "mfa_token": login["mfa_token"], "code": f"00000{i}"})
        statuses.append(response.status_code)
    assert statuses[-1] == 429, statuses
    assert all(st in (401, 429) for st in statuses), statuses

    # c5: сброс платформой → вход снова запускает setup
    users = client.get(f"{API}/users", headers=_auth(
        client.post(f"{API}/auth/login", json={
            "email": "admin@example.com",
            "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")
        }).json()["access_token"])).json()
    target = next(u for u in users if u["email"] == email)
    reset = client.post(f"{API}/platform/users/{target['id']}/totp/reset",
                        headers=_auth(platform["access_token"]))
    assert reset.status_code == 200, reset.text
    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": password}).json()
    assert login.get("totp_setup_required") is True, "после сброса — setup заново"
    assert not login.get("mfa_required"), "2FA выключена сбросом"


def test_c6_employee_setup_forbidden(client, platform):
    """Сотруднику setup — 403 (2FA только руководителям, решение основателя)."""
    body = _new_org(client, platform, "d")
    # сотрудник входит сразу парой
    org_ctx = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"],
        "company_id": body["id"]}).json()
    employee = client.post(f"{API}/users", json={
        "email": f"mt-c-emp2-{RUN}@mt.test", "password": "Employee2pass",
        "role": "user", "name": "emp2"}, headers=_auth(org_ctx["access_token"]))
    assert employee.status_code == 201
    emp = client.post(f"{API}/auth/login", json={
        "email": f"mt-c-emp2-{RUN}@mt.test", "password": "Employee2pass"}).json()
    response = client.post(f"{API}/auth/totp/setup", json={},
                           headers=_auth(emp["access_token"]))
    assert response.status_code == 403, response.text
