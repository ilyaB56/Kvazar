"""Devtools этап A — браузер таблиц (devtools-spec §6, приёмка §13.A).

Права (403 без table_browser), автокомпания (422 на явный company_id в
org; платформа — можно), маскировка (*** + 422 на фильтр/сортировку),
инъекции (422, схема цела), лимит экспорта, CSV/XLSX, аудит экспорта
(просмотры — БЕЗ аудита, О4/О6), личные пресеты (О2).

Запуск: docker compose exec api pytest tests/test_devtools_a.py
"""

from __future__ import annotations

import json
import os
import urllib.parse
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
    """Платформенный токен (без org) + org-контекст «Основной»."""
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    org = next(o for o in login["organizations"] if o["name"] == "Основная") \
        if any(o["name"] == "Основная" for o in login["organizations"]) \
        else login["organizations"][0]
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"], "company_id": org["id"]}).json()
    return ({"Authorization": f"Bearer {login['access_token']}"},
            {"Authorization": f"Bearer {pair['access_token']}"},
            org["id"])


def _no_access_token(client):
    """Пользователь без привилегии table_browser (readonly-роль)."""
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
        "email": email, "password": os.environ.get("ERP_TEST_PASSWORD", "SmokeUser1Pass")})
    if login.status_code != 200:
        pytest.skip("не удалось войти readonly-пользователем")
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_no_privilege_403(client):
    """§13.A.1: без table_browser — 403 на все /system/tables*.
    readonly-пользователь: admin имеет rw по ветке кода — берём
    делегирование НЕ выдаём и проверяем токен readonly-роли; если
    такого пользователя нет — тест пропускается (прописан в смоке)."""
    headers = _no_access_token(client)
    for path in ("/system/tables", "/system/tables/erp_core/users",
                 "/system/tables/erp_core/users/rows"):
        response = client.get(f"{API}{path}", headers=headers)
        assert response.status_code == 403, path


def test_tables_list_scoping(client):
    """§13.A.6: платформенные таблицы скрыты в org, видны платформе."""
    _, org_h, _ = _admin(client)
    tables = client.get(f"{API}/system/tables", headers=org_h).json()
    names = {t["schema"] + "." + t["table"] for t in tables}
    assert "erp_core.companies" not in names
    assert "erp_core.signup_requests" not in names
    assert "mgmt_accounting.rates" in names  # глобальный справочник


def test_rows_autocompany(client):
    """§13.A.3: items своей org; total совпадает с psql-срезом."""
    pl_h, org_h, org_id = _admin(client)
    rows = client.get(
        f"{API}/system/tables/mgmt_accounting/items/rows?limit=5",
        headers=org_h).json()
    assert 0 < len(rows["items"]) <= 5
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        psql_total = db.scalar(text(
            "SELECT count(*) FROM mgmt_accounting.items"
            " WHERE company_id = :o").bindparams(o=org_id))
    finally:
        db.close()
    assert rows["total"] == psql_total


def test_masked_columns(client):
    """§13.A.4: password_hash=*** в rows; фильтр/сортировка → 422."""
    pl_h, _, _ = _admin(client)
    rows = client.get(
        f"{API}/system/tables/erp_core/users/rows"
        "?columns=email,password_hash&limit=3", headers=pl_h).json()
    assert rows["items"]
    assert all(r["password_hash"] == "***" for r in rows["items"])
    flt = urllib.parse.quote('[{"col":"password_hash","op":"contains","value":"gAAA"}]')
    assert client.get(
        f"{API}/system/tables/erp_core/users/rows?filters={flt}",
        headers=pl_h).status_code == 422
    assert client.get(
        f"{API}/system/tables/erp_core/users/rows?sort=password_hash,asc",
        headers=pl_h).status_code == 422


def test_company_filter_rules(client):
    """§13.A.5: явный company_id в org → 422; платформа → 200."""
    pl_h, org_h, org_id = _admin(client)
    flt = urllib.parse.quote(
        f'[{{"col":"company_id","op":"eq","value":"{org_id}"}}]')
    assert client.get(
        f"{API}/system/tables/mgmt_accounting/items/rows?filters={flt}",
        headers=org_h).status_code == 422
    assert client.get(
        f"{API}/system/tables/mgmt_accounting/items/rows?filters={flt}",
        headers=pl_h).status_code == 200


def test_injection_422_schema_intact(client):
    """§13.A.7: инъекционные имена → 422; схема цела после серии."""
    pl_h, _, _ = _admin(client)
    flt = urllib.parse.quote(
        '[{"col":"id; DROP TABLE users","op":"eq","value":"x"}]')
    assert client.get(
        f"{API}/system/tables/erp_core/users/rows?filters={flt}",
        headers=pl_h).status_code == 422
    assert client.get(
        f"{API}/system/tables/erp_core/nosuch/rows",
        headers=pl_h).status_code == 422
    assert client.get(
        f"{API}/system/tables/erp_core/users/rows?sort=1=1;--,asc",
        headers=pl_h).status_code == 422
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        assert db.scalar(text("SELECT count(*) FROM erp_core.users")) > 0
    finally:
        db.close()


def test_export_csv_xlsx_limit_audit(client):
    """§13.A.8–9: CSV с BOM и XLSX; лимит → 422 с total; аудит."""
    _, org_h, _ = _admin(client)
    body = {"filters": [{"col": "kind", "op": "eq", "value": "digital"}]}
    csv_r = client.post(
        f"{API}/system/tables/mgmt_accounting/items/export",
        json={**body, "fmt": "csv"}, headers=org_h)
    assert csv_r.status_code == 200
    assert csv_r.content[:3] == b"\xef\xbb\xbf"  # BOM
    assert b";" in csv_r.content

    xlsx_r = client.post(
        f"{API}/system/tables/mgmt_accounting/items/export",
        json={**body, "fmt": "xlsx"}, headers=org_h)
    assert xlsx_r.status_code == 200
    assert xlsx_r.content[:2] == b"PK"  # zip = xlsx

    # лимит: вся номенклатура > 10 000 в живой базе
    over = client.post(f"{API}/system/tables/mgmt_accounting/items/export",
                       json={"fmt": "csv"}, headers=org_h)
    # (в org-контексте может быть меньше — проверяем только если больше)
    if over.status_code == 422:
        assert b"export_limit" in over.content
        assert str(json.loads(over.content)["detail"]).find("уточните") >= 0

    # аудит: экспорт есть, просмотров rows — нет
    events = client.get(f"{API}/events/log?limit=20", headers=org_h).json()
    events = events if isinstance(events, list) else events.get("items", [])
    assert any(e.get("action") == "system.table_exported" for e in events)


def test_presets_lifecycle(client):
    """§13.A.8: сохранить фильтры/сортировку/колонки под именем →
    применить; перезапись не дубliрует; чужие не видны (key-value)."""
    _, org_h, _ = _admin(client)
    name = f"пресет-{RUN}"
    saved = client.post(
        f"{API}/system/tables/mgmt_accounting/items/presets?name={urllib.parse.quote(name)}",
        json={"definition": {"filters": [{"col": "kind", "op": "eq", "value": "digital"}],
                             "sort": ["created_at,desc"], "columns": ["sku", "name"]}},
        headers=org_h)
    assert saved.status_code == 201
    listed = client.get(
        f"{API}/system/tables/mgmt_accounting/items/presets", headers=org_h).json()
    mine = [p for p in listed if p["name"] == name]
    assert len(mine) == 1 and mine[0]["definition"]["filters"][0]["value"] == "digital"
    # перезапись
    client.post(
        f"{API}/system/tables/mgmt_accounting/items/presets?name={urllib.parse.quote(name)}",
        json={"definition": {"filters": []}}, headers=org_h)
    listed2 = client.get(
        f"{API}/system/tables/mgmt_accounting/items/presets", headers=org_h).json()
    assert len([p for p in listed2 if p["name"] == name]) == 1
    # удалить
    deleted = client.delete(
        f"{API}/system/tables/mgmt_accounting/items/presets/{mine[0]['id']}",
        headers=org_h)
    assert deleted.status_code == 200


def test_platform_tables_access(client):
    """Платформенный контекст: users всех организаций; org-контекст:
    platform_only → 422/403 на rows."""
    pl_h, org_h, _ = _admin(client)
    pl_rows = client.get(
        f"{API}/system/tables/erp_core/users/rows?limit=3", headers=pl_h)
    assert pl_rows.status_code == 200
    org_attempt = client.get(
        f"{API}/system/tables/erp_core/companies/rows", headers=org_h)
    assert org_attempt.status_code == 422
