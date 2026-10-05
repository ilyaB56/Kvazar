"""Devtools этап C — отладчик (devtools-spec §8, приёмка §13.C).

Права (403 без devtools), org-изоляция (audit: org не видит чужие;
outbox по payload.company_id), фильтры, пагинация, трассировка
(версии+аудит+outbox), диагностика (без секретов подключений).

Запуск: docker compose exec api pytest tests/test_devtools_c.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=120)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running")
    yield http
    http.close()


def _admin(client):
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    org = next((o for o in login["organizations"] if o["name"] == "Основная"),
               login["organizations"][0])
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"], "company_id": org["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}, org["id"]


def test_no_devtools_403(client):
    """§13.C.1: без devtools → 403 на все /devtools/*."""
    from src.db import SessionLocal
    from src.core.models import User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(
            User.role == "readonly", User.is_active.is_(True)).limit(1))
        if user is None:
            pytest.skip("нет readonly-пользователя")
        email = user.email
    finally:
        db.close()
    login = client.post(f"{API}/auth/login", json={
        "email": email,
        "password": os.environ.get("ERP_TEST_PASSWORD", "SmokeUser1Pass")})
    if login.status_code != 200:
        pytest.skip("не удалось войти readonly-пользователем")
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for path in ("/devtools/logs?source=audit", "/devtools/diagnostics",
                 "/devtools/trace/user/00000000-0000-0000-0000-000000000000"):
        response = client.get(f"{API}{path}", headers=headers)
        assert response.status_code == 403, path


def test_logs_sources_and_filters(client):
    """§13.C.2/4: шесть источников; фильтры; пагинация."""
    headers, org_id = _admin(client)
    for source in ("audit", "egress", "outbox", "sync", "flow", "webhooks"):
        response = client.get(
            f"{API}/devtools/logs?source={source}&limit=2", headers=headers)
        assert response.status_code == 200, source
        body = response.json()
        assert set(body) == {"items", "total"} and len(body["items"]) <= 2

    # фильтры: sync status + flow error ILIKE
    sync = client.get(f"{API}/devtools/logs?source=sync&status=error&limit=5",
                      headers=headers).json()
    assert all(item["status"] == "error" for item in sync["items"])
    flow = client.get(
        f"{API}/devtools/logs?source=flow&error=insufficient&limit=5",
        headers=headers).json()
    assert all("insufficient" in (item.get("error") or "").lower()
               for item in flow["items"])
    assert client.get(f"{API}/devtools/logs?source=nosuch",
                      headers=headers).status_code == 422


def test_trace_versions_audit_outbox(client):
    """§13.C.3: известная сущность — версии + аудит (+outbox при
    наличии событий). Берём свежую категорию, правленную ракурсом."""
    headers, _ = _admin(client)
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT DISTINCT rv.entity_type, rv.entity_id"
            " FROM erp_core.record_versions rv"
            " JOIN erp_core.events_log el"
            "   ON el.entity_id = rv.entity_id AND el.entity_type = rv.entity_type"
            " ORDER BY 2 DESC LIMIT 1")).first()
    finally:
        db.close()
    assert row is not None, "нет сущности с версиями и аудитом"
    entity_type, entity_id = row

    trace = client.get(
        f"{API}/devtools/trace/{entity_type}/{entity_id}",
        headers=headers).json()
    assert trace["entity_type"] == entity_type
    sources = {item["source"] for item in trace["timeline"]}
    assert "version" in sources and "audit" in sources
    # хронология desc
    times = [item["at"] for item in trace["timeline"] if item.get("at")]
    assert times == sorted(times, reverse=True)


def test_diagnostics(client):
    """§13.C.5: версии/здоровье/падения; секреты подключений отсутствуют."""
    headers, _ = _admin(client)
    diag = client.get(f"{API}/devtools/diagnostics", headers=headers).json()
    assert diag["version"]
    assert any(m["name"] == "core" for m in diag["modules"])
    assert diag["health"]["db"]["ok"] is True
    assert isinstance(diag["health"]["outbox"]["pending"], int)
    assert "connections" in diag and "recent_failures" in diag
    # без секретов: у подключений только имена/статусы
    for connection in diag["connections"]:
        assert "credentials" not in connection
        assert "api_key" not in str(connection).lower()


def test_logs_no_self_audit(client):
    """§13.C.6: чтение журналов не создаёт записей в events_log."""
    headers, _ = _admin(client)
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        before = db.scalar(text(
            "SELECT count(*) FROM erp_core.events_log"))
    finally:
        db.close()
    for _ in range(3):
        client.get(f"{API}/devtools/logs?source=outbox&limit=5",
                   headers=headers)
    db = SessionLocal()
    try:
        after = db.scalar(text("SELECT count(*) FROM erp_core.events_log"))
    finally:
        db.close()
    assert after == before, "чтение журналов журналируется (нарушение О6)"


def test_outbox_org_isolation(client):
    """§13.C.2: org-контекст — только свои события шины."""
    headers, org_id = _admin(client)
    body = client.get(f"{API}/devtools/logs?source=outbox&limit=50",
                      headers=headers).json()
    for item in body["items"]:
        payload_company = (item.get("payload") or {}).get("company_id")
        if payload_company is not None:
            assert str(payload_company) == str(org_id)
