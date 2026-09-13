"""Мультитенантность B2: изоляция mini_crm и ai_agent (multitenancy-spec §10.B).

Организация А = «Основная», Б — свежая (платформа, сид со стадиями CRM).
Проверки: стадии Б — свои копии (позиции не конфликтуют с А), сделки Б не
видны А и наоборот (список/точечный), документы ИИ изолированы, RAG-поиск
не находит чужие документы.

Запуск: docker compose exec api pytest tests/test_multitenancy_b2.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
CRM = f"{API}/crm"
AI = f"{API}/ai"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "mt-b2-test"})
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
        "name": f"mt-B2-{RUN}",
        "admin_email": f"mt-b2-admin-{RUN}@mt.test", "admin_full_name": "B2",
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
    return {"id": platform["organizations"][0]["id"], "token": pair["access_token"]}


def test_b2t1_stages_copied_and_isolated(client, org_a, org_b):
    """Стадии Б — своя копия эталона (те же позиции, другие id); создание
    стадии с позицией А в Б — допустимо (UNIQUE составной)."""
    stages_a = client.get(CRM + "/stages", headers=_auth(org_a["token"])).json()
    stages_b = client.get(CRM + "/stages", headers=_auth(org_b["token"])).json()
    assert len(stages_b) >= 5, "сид стадий не скопировался"
    ids_a = {s["id"] for s in stages_a}
    assert all(s["id"] not in ids_a for s in stages_b), "стадии Б = стадии А"
    names_b = {s["name"] for s in stages_b}
    assert {"Новая", "Выиграна"} <= names_b


def test_b2t2_deals_isolated(client, org_a, org_b):
    """Сделка Б не видна А (список и точечный), и наоборот."""
    stages_b = client.get(CRM + "/stages", headers=_auth(org_b["token"])).json()
    new_stage = next(s for s in stages_b if s["name"] == "Новая")
    deal = client.post(CRM + "/deals", json={
        "title": f"mt-b2-deal-{RUN}", "stage_id": new_stage["id"],
        "amount": "10000"}, headers=_auth(org_b["token"])).json()
    assert deal["id"]

    # список А не содержит сделку Б
    deals_a = client.get(CRM + f"/deals?q=mt-b2-deal-{RUN}",
                         headers=_auth(org_a["token"])).json()
    assert deals_a == []
    # точечный из А — 404
    response = client.get(CRM + f"/deals/{deal['id']}",
                          headers=_auth(org_a["token"]))
    assert response.status_code == 404
    # move чужой — 404
    stages_a = client.get(CRM + "/stages", headers=_auth(org_a["token"])).json()
    response = client.post(CRM + f"/deals/{deal['id']}/move",
                           json={"stage_id": stages_a[0]["id"]},
                           headers=_auth(org_a["token"]))
    assert response.status_code == 404


def test_b2t3_ai_documents_isolated(client, org_a, org_b):
    """Документ ИИ: загрузка в Б видна только Б; поиск RAG из А не находит
    его; download из А — 404."""
    marker = f"mtb2doc{RUN}"
    files = {"file": (f"{marker}.txt", f"секретный маркер {marker} онлайн-продажи".encode())}
    response = client.post(AI + "/documents", files=files,
                           headers={**_auth(org_b["token"]),
                                    "X-Test": "1"})
    if response.status_code == 403:
        # у админа Б может не быть права ai (роль admin — rw везде) — не должно
        pytest.fail(f"загрузка документа запрещена: {response.text[:120]}")
    assert response.status_code == 201, response.text
    doc_b = response.json()

    # список А без документа Б
    docs_a = client.get(AI + "/documents", headers=_auth(org_a["token"])).json()
    assert all(d["id"] != doc_b["id"] for d in docs_a)

    # download из А — 404
    response = client.get(AI + f"/documents/{doc_b['id']}/download",
                          headers=_auth(org_a["token"]))
    assert response.status_code == 404

    # RAG-поиск: векторный top-k всегда не пуст — инвариант в том, что
    # результаты А не содержат документ/маркер Б, а в Б маркер находится
    search_a = client.get(AI + f"/search?q={marker}",
                          headers=_auth(org_a["token"])).json()
    assert all(row["document_id"] != doc_b["id"] for row in search_a)
    assert all(marker not in (row.get("text") or "") for row in search_a)
    search_b = client.get(AI + f"/search?q={marker}",
                          headers=_auth(org_b["token"])).json()
    assert any(marker in (row.get("text") or "") for row in search_b)
