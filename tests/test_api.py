"""Integration-тесты API: auth + каталог коннекторов.

Требуют запущенных зависимостей (docker compose up db redis) и применённых
миграций. Пропускаются, если БД недоступна: @pytest.mark.integration.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    client = httpx.Client(base_url=BASE_URL, timeout=10)
    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError:
        client.close()
        pytest.skip("API not running (docker compose up)")
    yield client
    client.close()


@pytest.fixture(scope="module")
def admin_token(client):
    response = client.post("/api/v1/auth/login", json={
        "email": "admin@example.com", "password": "admin12345",
    })
    response.raise_for_status()

    # мультитенантность: админ — супер-админ платформы; модульные данные —
    # в контексте организации «Основная» (без org — 403 no_company_context)
    _orgs = response.json().get("organizations") or []
    if _orgs:
        _oid = next((o["id"] for o in _orgs if o.get("name") == "Основная"),
                    _orgs[0]["id"])
        response = client.post("/api/v1/auth/select-org", json={
            "refresh_token": response.json()["refresh_token"],
            "company_id": _oid})
        response.raise_for_status()
    return response.json()["access_token"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_modules(client):
    names = [m["name"] for m in client.get("/api/v1/modules").json()]
    assert "core" in names and "integrations" in names


def test_connectors_catalog(client, admin_token):
    response = client.get("/api/v1/integrations/connectors",
                          headers={"Authorization": f"Bearer {admin_token}"})
    response.raise_for_status()
    codes = [c["code"] for c in response.json()]
    assert "http_rest" in codes and "bank_api" in codes


def test_webhook_flow(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post("/api/v1/integrations/webhooks",
                          json={"name": "test hook"}, headers=headers)
    created.raise_for_status()
    hook = created.json()

    # неверный токен отклоняется
    bad = client.post(hook["url_path"], json={}, headers={"X-ERP-Token": "wrong"})
    assert bad.status_code == 401

    # верный токен принимается
    ok = client.post(hook["url_path"], json={"event": "payment"},
                     headers={"X-ERP-Token": hook["secret_token"]})
    assert ok.status_code == 202
