"""Мультитенантность B3: изоляция integrations (multitenancy-spec §10.B/Р4).

Подключения/вебхуки/правила уведомлений организации Б невидимы А;
платформенный коннектор (NULL, ЦБ РФ) виден только супер-админу платформы.

Запуск: docker compose exec api pytest tests/test_multitenancy_b3.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
INT = f"{API}/integrations"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "mt-b3-test"})
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


@pytest.fixture(scope="module")
def org_b(client, platform):
    response = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-B3-{RUN}",
        "admin_email": f"mt-b3-admin-{RUN}@mt.test", "admin_full_name": "B3",
    }, headers=_auth(platform["access_token"]))
    assert response.status_code == 201, response.text
    body = response.json()
    login = client.post(f"{API}/auth/login", json={
        "email": body["admin"]["email"], "password": body["temp_password"]})
    assert login.status_code == 200, login.text
    return {"id": body["id"], "token": login.json()["access_token"]}


@pytest.fixture(scope="module")
def org_a(client, platform):
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"],
        "company_id": platform["organizations"][0]["id"]}).json()
    return {"id": platform["organizations"][0]["id"],
            "token": pair["access_token"],
            "platform_token": platform["access_token"]}


def test_b3t1_connections_isolated(client, org_a, org_b):
    """Подключение Б невидимо А (и наоборот); ЦБ РФ (платформенный, NULL)
    виден только супер-админу платформы, не организации."""
    # Б создаёт своё подключение (http_rest на несуществующий хост — ок для записи)
    own = client.post(INT + "/connections", json={
        "name": f"mt-b3-conn-{RUN}", "connector_code": "http_rest",
        "credentials": {"api_key": "x"},
        "config": {"base_url": "http://localhost:1"},
    }, headers=_auth(org_b["token"]))
    assert own.status_code == 201, own.text
    conn_b = own.json()

    conns_a = client.get(INT + "/connections",
                         headers=_auth(org_a["token"])).json()
    assert all(c["id"] != conn_b["id"] for c in conns_a)

    # ЦБ РФ: у организации Б в списке отсутствует, у pl в контексте org — есть
    names_b = {c["name"] for c in client.get(
        INT + "/connections", headers=_auth(org_b["token"])).json()}
    assert "ЦБ РФ" not in names_b, "платформенный коннектор виден организации"
    names_pl = {c["name"] for c in client.get(
        INT + "/connections", headers=_auth(org_a["token"])).json()}
    assert "ЦБ РФ" in names_pl


def test_b3t2_rules_and_webhooks_isolated(client, org_a, org_b):
    """Правило уведомления Б не видится А; вебхуки Б изолированы."""
    rule = client.post(INT + "/notification-rules", json={
        "name": f"mt-b3-rule-{RUN}", "event_name": "acc.transaction.posted",
        "chat_id": "0", "template": "mt"},
        headers=_auth(org_b["token"]))
    assert rule.status_code == 201, rule.text

    rules_a = client.get(INT + "/notification-rules",
                         headers=_auth(org_a["token"])).json()
    assert all(r["id"] != rule.json()["id"] for r in rules_a)

    hook = client.post(INT + "/webhooks", json={
        "name": f"mt-b3-hook-{RUN}", "target_module": "payments"},
        headers=_auth(org_b["token"]))
    assert hook.status_code == 201, hook.text
    hooks_a = client.get(INT + "/webhooks",
                         headers=_auth(org_a["token"])).json()
    assert all(h["id"] != hook.json()["id"] for h in hooks_a)
