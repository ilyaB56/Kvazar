"""Devtools этап B — ракурсы ведения (devtools-spec §7, приёмка §13.B).

Чёрный/белый списки, guarded-фильтр, direct правки/создание с версиями
и аудитом, запрет company_id, домен-адаптер контрагентов (дубль ИНН),
шаблоны платформы, ro vs rw.

Запуск: docker compose exec api pytest tests/test_devtools_b.py
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
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    org = next((o for o in login["organizations"] if o["name"] == "Основная"),
               login["organizations"][0])
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"], "company_id": org["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


def _mk_view(client, headers, name, schema, table, mode, definition):
    response = client.post(f"{API}/system/maintenance-views", json={
        "name": name, "table_schema": schema, "table_name": table,
        "mode": mode, "definition": definition}, headers=headers)
    return response


def test_forbidden_tables(client):
    """§13.B.1: чёрный список → 422 table_forbidden."""
    headers = _admin(client)
    cases = [
        ("mgmt_accounting", "transactions"), ("mgmt_accounting", "stock_moves"),
        ("mgmt_accounting", "shipments"), ("erp_core", "users"),
        ("integrations", "connections"), ("erp_core", "events_log"),
    ]
    for schema, table in cases:
        response = _mk_view(client, headers, f"запрет-{table}-{RUN}",
                            schema, table, "direct", {})
        assert response.status_code == 422, (schema, table)
        assert "table_forbidden" in response.text


def test_direct_whitelist_and_guarded(client):
    """§13.B.1: direct не из белого списка → 422; §13.B.5: locations без
    guarded → 422 guarded_filter_required; с фильтром — создаётся."""
    headers = _admin(client)
    assert _mk_view(client, headers, f"не-белый-{RUN}", "mini_crm", "deals",
                    "direct", {}).status_code == 422
    assert _mk_view(client, headers, f"склады-{RUN}", "mgmt_accounting",
                    "locations", "direct", {}).status_code == 422
    ok = _mk_view(client, headers, f"склады-ok-{RUN}", "mgmt_accounting",
                  "locations", "direct",
                  {"where": [{"col": "is_transit", "op": "eq", "value": False}]})
    assert ok.status_code == 201
    # транзитные не видны
    rows = client.get(
        f"{API}/system/maintenance-views/{ok.json()['id']}/rows?limit=200",
        headers=headers).json()
    assert all(not r.get("is_transit", False) for r in rows["items"])
    # зачистка
    client.delete(f"{API}/system/maintenance-views/{ok.json()['id']}",
                  headers=headers)


def test_direct_crud_versions_audit(client):
    """§13.B.2–4: правка name → версия+аудит; company_id → 422; создание
    с автокомпанией."""
    headers = _admin(client)
    created = _mk_view(client, headers, f"Статьи {RUN}", "mgmt_accounting",
                       "categories", "direct", {"columns": [
                           {"name": "name", "visible": True, "editable": True},
                           {"name": "kind", "visible": True, "editable": True}]})
    assert created.status_code == 201, created.text
    vid = created.json()["id"]

    rows = client.get(f"{API}/system/maintenance-views/{vid}/rows?limit=1",
                      headers=headers).json()
    pk, old_name = rows["items"][0]["id"], rows["items"][0]["name"]

    # правка
    patched = client.patch(
        f"{API}/system/maintenance-views/{vid}/rows/{pk}",
        json={"name": old_name + "-ракурс"}, headers=headers)
    assert patched.status_code == 200, patched.text
    # вернуть
    client.patch(f"{API}/system/maintenance-views/{vid}/rows/{pk}",
                 json={"name": old_name}, headers=headers)

    # company_id нередактируем
    assert client.patch(
        f"{API}/system/maintenance-views/{vid}/rows/{pk}",
        json={"company_id": "00000000-0000-0000-0000-000000000001"},
        headers=headers).status_code == 422
    # нередактируемая (internal_code — служебная)
    assert client.patch(
        f"{API}/system/maintenance-views/{vid}/rows/{pk}",
        json={"internal_code": "HACK"}, headers=headers).status_code == 422

    # создание: company_id автоматически
    new_row = client.post(f"{API}/system/maintenance-views/{vid}/rows",
                          json={"name": f"тест-b-{RUN}", "kind": "expense"},
                          headers=headers)
    assert new_row.status_code == 201

    # версии + аудит
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        versions = db.execute(text(
            "SELECT count(*) FROM erp_core.record_versions"
            " WHERE entity_type = 'categories'"
            " AND reason = 'maintenance_view'")).scalar()
        audits = db.execute(text(
            "SELECT count(*) FROM erp_core.events_log"
            " WHERE action IN ('system.view_row_updated','system.view_row_created')"
            " AND payload->>'view_id' = :v").bindparams(v=str(vid))).scalar()
        assert versions >= 2  # правка туда-обратно
        assert audits >= 3  # 2 правки + создание
    finally:
        db.close()

    client.delete(f"{API}/system/maintenance-views/{vid}", headers=headers)


def test_domain_counterparty_dedup(client):
    """§13.B.6: домен-ракурс; дубль ИНН → 422 от доменной валидации."""
    headers = _admin(client)
    created = _mk_view(client, headers, f"Контрагенты {RUN}",
                       "mgmt_accounting", "counterparties", "domain",
                       {"columns": [{"name": "name", "visible": True, "editable": True},
                                    {"name": "inn", "visible": True, "editable": True}]})
    assert created.status_code == 201, created.text
    vid = created.json()["id"]

    rows = client.get(f"{API}/system/maintenance-views/{vid}/rows?limit=50",
                      headers=headers).json()
    with_inn = [r for r in rows["items"] if r.get("inn")]
    if len(with_inn) >= 2:
        a, b = with_inn[0], with_inn[1]
        dup = client.patch(
            f"{API}/system/maintenance-views/{vid}/rows/{a['id']}",
            json={"inn": b["inn"]}, headers=headers)
        assert dup.status_code == 422
        assert "inn_kpp_duplicate" in dup.text
    # успешная правка без дубля
    ok = client.patch(
        f"{API}/system/maintenance-views/{vid}/rows/{rows['items'][0]['id']}",
        json={"name": rows["items"][0]["name"]}, headers=headers)
    assert ok.status_code == 200
    client.delete(f"{API}/system/maintenance-views/{vid}", headers=headers)


def test_platform_tables_from_org_forbidden(client):
    """§13.B.10: units/doc_types — платформенные direct-таблицы; из org
    → 422 platform_context_required."""
    headers = _admin(client)
    for table in ("units", "doc_types"):
        response = _mk_view(client, headers, f"глоб-{table}-{RUN}",
                            "mgmt_accounting", table, "direct", {})
        assert response.status_code == 422, table
        assert "platform" in response.text


def test_view_config_versions(client):
    """§13.B.9: изменение ракурса — record_versions maintenance_view."""
    headers = _admin(client)
    created = _mk_view(client, headers, f"верс-{RUN}", "mgmt_accounting",
                       "categories", "direct", {"columns": [
                           {"name": "name", "visible": True, "editable": True}]})
    vid = created.json()["id"]
    patched = client.patch(f"{API}/system/maintenance-views/{vid}",
                           json={"name": f"верс-2-{RUN}"}, headers=headers)
    assert patched.status_code == 200
    from src.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        count = db.execute(text(
            "SELECT count(*) FROM erp_core.record_versions"
            " WHERE entity_type = 'maintenance_view'"
            " AND entity_id = :v").bindparams(v=str(vid))).scalar()
        assert count >= 2  # create + update
    finally:
        db.close()
    client.delete(f"{API}/system/maintenance-views/{vid}", headers=headers)
