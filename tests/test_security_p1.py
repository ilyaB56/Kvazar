"""Security-plan P1: строгий egress-режим + ротация ключей (приёмка).

1. strict=true: запрос к api-seller.ozon.ru (в allowlist) проходит,
   к evil.example.com — EgressBlocked с сообщением про платформенного
   админа; PUT /platform/egress-settings применяет изменения сразу.
2. Ротация SECRETS_KEY (скрипт): connection-секрет, серийник и
   reset-токен перешифровываются и остаются рабочими.

Запуск: docker compose exec api pytest tests/test_security_p1.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration


def _platform(client):
    """Платформенный токен + org-контекст (создание connection)."""
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"],
        "company_id": login["organizations"][0]["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


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


def test_egress_strict_mode(client):
    from src.modules.integrations.connectors.egress import (
        EgressBlocked, check_egress, reset_egress_cache,
    )

    headers = _platform(client)
    before = client.get(f"{API}/platform/egress-settings", headers=headers).json()
    try:
        allowlist = sorted(set(before["allowlist"])
                           | {"api-seller.ozon.ru", "api"})
        allowlist = [d for d in allowlist if d != "evil.example.com"]
        put = client.put(f"{API}/platform/egress-settings", json={
            "allowlist": allowlist, "strict": True}, headers=headers)
        assert put.status_code == 200, put.text
        assert put.json()["strict"] is True
        reset_egress_cache()

        check_egress("https://api-seller.ozon.ru/v3/product/list")
        check_egress("http://api:8000/api/v1/health")
        with pytest.raises(EgressBlocked) as exc:
            check_egress("https://evil.example.com/v1/data")
        assert "не в белом списке" in str(exc.value)
        assert "платформенного админа" in str(exc.value)

        # добавление домена через PUT разблокирует
        allowlist2 = sorted(set(allowlist) | {"evil.example.com"})
        put2 = client.put(f"{API}/platform/egress-settings", json={
            "allowlist": allowlist2, "strict": True}, headers=headers)
        assert put2.status_code == 200
        reset_egress_cache()  # кэш pytest-процесса, PUT сбросил только api
        check_egress("https://evil.example.com/v1/data")

        # без платформенного токена — 401/403
        anon = client.put(f"{API}/platform/egress-settings", json={
            "allowlist": [], "strict": False})
        assert anon.status_code in (401, 403)
    finally:
        client.put(f"{API}/platform/egress-settings", json={
            "allowlist": before["allowlist"], "strict": before["strict"]},
            headers=headers)
        reset_egress_cache()


def test_http_rest_egress_blocked(client):
    """HttpRestConnector ходит через egress-контроль: strict + незнакомый
    домен → EgressBlocked в ConnectorResult.error (не сырой httpx)."""
    from src.modules.integrations.connectors.builtin import registry
    from src.modules.integrations.connectors.egress import reset_egress_cache

    headers = _platform(client)
    before = client.get(f"{API}/platform/egress-settings", headers=headers).json()
    try:
        allowlist = [d for d in before["allowlist"] if d not in ("evil.example.com",)]
        put = client.put(f"{API}/platform/egress-settings", json={
            "allowlist": allowlist, "strict": True}, headers=headers)
        assert put.status_code == 200
        reset_egress_cache()

        conn = registry.build("http_rest",
                              {"base_url": "https://evil.example.com/api",
                               "auth_style": "none"}, {})
        result = conn.fetch("/data")
        assert not result.ok
        assert "не в белом списке" in result.error
        assert "платформенного админа" in result.error
        probe = conn.test_connection()
        assert not probe.ok and "не в белом списке" in probe.error
    finally:
        client.put(f"{API}/platform/egress-settings", json={
            "allowlist": before["allowlist"], "strict": before["strict"]},
            headers=headers)
        reset_egress_cache()


def test_rotate_secrets_key(client):
    """Ротация SECRETS_KEY: credentials_enc + code_enc + token_enc
    перешифрованы новым ключом; обратная ротация возвращает env-ключ —
    connection продолжает работать (test_connection ok)."""
    from cryptography.fernet import Fernet

    from src.core.models import PasswordReset
    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from sqlalchemy import select
    from src.modules.mgmt_accounting.features.inventory import models as inv_m

    headers = _platform(client)
    conn_resp = client.post(f"{API}/integrations/connections", json={
        "name": f"rotate-{RUN}", "connector_code": "http_rest",
        "credentials": {"api_key": f"sk-{RUN}"},
        "config": {"base_url": "http://api:8000", "auth_style": "none"},
    }, headers=headers)
    assert conn_resp.status_code == 201, conn_resp.text
    conn = conn_resp.json()
    client.post(f"{API}/auth/forgot-password", json={"login": "admin@example.com"})

    db = SessionLocal()
    try:
        serial = db.scalar(select(inv_m.ItemSerial).limit(1))
        reset_row = db.scalar(select(PasswordReset)
                              .order_by(PasswordReset.created_at.desc()))
        assert reset_row is not None

        new_key = Fernet.generate_key().decode()
        result = subprocess.run(
            [sys.executable, "/app/scripts/rotate_secrets_key.py",
             "--new-key", new_key],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

        new_f = Fernet(new_key.encode())
        db.expire_all()
        conn_row = db.get(im.Connection, conn["id"])
        assert new_f.decrypt(conn_row.credentials_enc.encode())
        if serial is not None:
            db.refresh(serial)
            assert new_f.decrypt(serial.code_enc.encode())
        db.refresh(reset_row)
        assert new_f.decrypt(reset_row.token_enc.encode())
    finally:
        # обратная ротация: new_key → env SECRETS_KEY
        from src.config import get_settings

        back = subprocess.run(
            [sys.executable, "/app/scripts/rotate_secrets_key.py",
             "--old-key", new_key, "--new-key", get_settings().secrets_key],
            capture_output=True, text=True, timeout=120)
        assert back.returncode == 0, back.stdout + back.stderr
        db.close()

    test = client.post(f"{API}/integrations/connections/{conn['id']}/test",
                       headers=headers)
    assert test.status_code == 200 and test.json()["ok"], test.text
