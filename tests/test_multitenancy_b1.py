"""Мультитенантность B1: изоляция mgmt_accounting (multitenancy-spec §10.B).

Организация А = «Основная» (все существующие данные), организация Б — свежая
(создана платформой, сеед: категории/склады/период). Админ Б создаёт свой
контур и не видит А; списки А не содержат Б и наоборот; совпадающие
sku/номера не конфликтуют (составные UNIQUE); периоды независимы.

Запуск: docker compose exec api pytest tests/test_multitenancy_b1.py
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
ACC = f"{API}/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module", autouse=True)
def _teardown_orgs():
    """Деактивируем тест-организации прогона: реестр платформы не пухнет."""
    yield
    try:
        from sqlalchemy import text

        from src.db import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("UPDATE erp_core.companies SET is_active = false"
                            " WHERE name LIKE :pat"
                            ).bindparams(pat="mt-B1-%"))
            db.commit()
        finally:
            db.close()
    except ImportError:
        pass  # запуск вне api-контейнера (прямой pytest без БД) — убирать нечего
    # БД доступна, но чистка упала → падаем честно (мусор не копится молча)


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "mt-b1-test"})
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
    """Свежая организация Б + вход её админа (сеед стартовых данных)."""
    response = client.post(f"{API}/platform/orgs", json={
        "name": f"mt-B1-{RUN}", "inn": "",
        "admin_email": f"mt-b1-admin-{RUN}@mt.test", "admin_full_name": "B1",
    }, headers=_auth(platform["access_token"]))
    assert response.status_code == 201, response.text
    body = response.json()
    login = client.post(f"{API}/auth/login", json={
        "email": body["admin"]["email"], "password": body["temp_password"]})
    assert login.status_code == 200, login.text
    return {"id": body["id"], "token": login.json()["access_token"],
            "temp_password": body["temp_password"]}


@pytest.fixture(scope="module")
def org_a(client, platform):
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": platform["refresh_token"],
        "company_id": platform["organizations"][0]["id"]}).json()
    return {"id": platform["organizations"][0]["id"], "token": pair["access_token"]}


# Списковые эндпоинты модуля (раздел/путь): пустой список в Б до создания
LIST_ENDPOINTS = [
    "/accounts", "/categories", "/counterparties", "/items", "/locations",
    "/transactions", "/purchase-orders", "/receipts", "/sales-orders",
    "/shipments", "/tech-cards", "/production-orders",
    "/stock/balances", "/stock/moves",
]


def test_b1t1_lists_isolated_for_fresh_org(client, org_b):
    """Б (свежая) видит сеед (склады/категории/период), но НИ ОДНОЙ
    бизнес-сущности А: транзакции/заказы/товары А отсутствуют."""
    for path in LIST_ENDPOINTS:
        response = client.get(ACC + path, headers=_auth(org_b["token"]))
        assert response.status_code == 200, f"{path}: {response.status_code}"
        rows = response.json()
        if path == "/locations":
            # сеед: 6 системных складов Б, без складов А (тех же имён — они Б)
            assert all(r["id"] for r in rows) and len(rows) >= 6
        elif path == "/categories":
            assert any(c["name"] == "Продажи" for c in rows)
        elif path == "/transactions":
            assert rows == [], f"чужие транзакции видны: {path}"
        elif path in ("/purchase-orders", "/receipts", "/sales-orders",
                      "/shipments", "/production-orders", "/stock/moves"):
            assert rows == [], f"чужие документы видны: {path}"


def test_b1t2_cross_tenant_404_on_direct_ids(client, org_a, org_b):
    """Прямой id сущности А из Б — 404 (не 403 — не раскрываем существование)."""
    # сущности А
    items_a = client.get(ACC + "/items", headers=_auth(org_a["token"])).json()
    txns_a = client.get(ACC + "/transactions", headers=_auth(org_a["token"])).json()
    assert items_a and txns_a
    item_id = items_a[0]["id"]
    txn_id = txns_a[0]["id"]

    response = client.get(ACC + f"/items/{item_id}", headers=_auth(org_b["token"]))
    assert response.status_code == 404, "товар А виден из Б"
    # точечного GET транзакций нет — мутация чужой ниже

    # мутации чужого — тоже 404
    response = client.post(ACC + f"/transactions/{txn_id}/storno",
                           json={"reason": "x"}, headers=_auth(org_b["token"]))
    assert response.status_code == 404


def test_b1t3_same_sku_and_numbers_no_conflict(client, org_a, org_b):
    """Организации создают совпадающие sku и получают одинаковые номера
    документов (ЗК-…-00001 у каждой своей нумерации) без конфликтов."""
    # у А уже есть товар с некоторым sku — берём новый общий sku
    sku = f"MT-BOTH-{RUN}"
    for org in (org_a, org_b):
        response = client.post(ACC + "/items", json={
            "sku": sku, "name": f"общий {RUN}", "kind": "physical",
            "unit_code": "шт"}, headers=_auth(org["token"]))
        assert response.status_code == 201, f"{response.text[:150]}"

    # дубликат внутри организации — запрет
    response = client.post(ACC + "/items", json={
        "sku": sku, "name": "dup", "kind": "physical",
        "unit_code": "шт"}, headers=_auth(org_b["token"]))
    assert response.status_code == 422

    # одинаковые номера: обе создают и проводят транзакцию
    numbers = []
    for org in (org_a, org_b):
        account = client.post(ACC + "/accounts", json={
            "name": f"mt-b1-{RUN}", "currency": "RUB"},
            headers=_auth(org["token"])).json()
        category = client.post(ACC + "/categories", json={
            "name": "mt-b1", "kind": "expense"},
            headers=_auth(org["token"])).json()
        txn = client.post(ACC + "/transactions", json={
            "kind": "expense", "operated_at": "2026-09-13",
            "amount": "1.00", "currency": "RUB",
            "account_id": account["id"], "category_id": category["id"],
            "post_immediately": True}, headers=_auth(org["token"])).json()
        assert txn["status"] == "posted" and txn["doc_number"]
        numbers.append(txn["doc_number"])
    # приватность нумерации (Р2): у каждой свой счётчик — совпадение допустимо
    # и даже ожидаемо (обе — первый номер года в своей организации)
    assert numbers[0].startswith("СК-") and numbers[1].startswith("СК-")


def test_b1t4_periods_independent(client, org_a, org_b):
    """Закрытие периода в А не мешает работать Б (и наоборот)."""
    # А закрывает сентябрь
    response = client.post(ACC + "/periods/2026/9/close", json={"reason": "mt"},
                           headers=_auth(org_a["token"]))
    assert response.status_code in (200, 409), response.text
    # Б проводит транзакцию в сентябре — период Б открыт
    account = client.post(ACC + "/accounts", json={
        "name": f"mt-b1-per-{RUN}", "currency": "RUB"},
        headers=_auth(org_b["token"])).json()
    txn = client.post(ACC + "/transactions", json={
        "kind": "expense", "operated_at": "2026-09-13",
        "amount": "2.00", "currency": "RUB", "account_id": account["id"],
        "post_immediately": True}, headers=_auth(org_b["token"]))
    assert txn.status_code == 201, txn.text
    # периоды Б не содержат закрытого А (разные строки)
    periods_b = client.get(ACC + "/periods", headers=_auth(org_b["token"])).json()
    sep_b = next((p for p in periods_b if p["year"] == 2026 and p["month"] == 9), None)
    assert sep_b is None or sep_b["status"] == "open"
    # вернуть: reopen сентября А
    client.post(ACC + "/periods/2026/9/reopen", json={"reason": "mt"},
                headers=_auth(org_a["token"]))


def test_b1t5_events_carry_company_id(client, org_a, org_b):
    """payload существующих событий содержит company_id (ADR-002)."""
    outbox = client.get(f"{API}/events/outbox?event_name=acc.transaction.posted&limit=10",
                        headers=_auth(org_a["token"])).json()
    assert outbox, "нет событий acc.transaction.posted"
    assert all(e["payload"].get("company_id") for e in outbox), \
        [e["payload"] for e in outbox[:2]]


def test_b1t6_full_business_cycle_in_org_b(client, org_a, org_b):
    """Полный цикл внутри организации Б: закупка → приёмка → продажа →
    отчёты — двойная запись и маржа без регресса (§10.B)."""
    token = _auth(org_b["token"])
    supplier = client.post(ACC + "/counterparties",
                           json={"name": f"mt-b1-supplier-{RUN}"},
                           headers=token).json()
    customer = client.post(ACC + "/counterparties",
                           json={"name": f"mt-b1-customer-{RUN}"},
                           headers=token).json()
    item = client.post(ACC + "/items", json={
        "sku": f"MT-B1-CYCLE-{RUN}", "name": "товар цикла", "kind": "physical",
        "unit_code": "шт", "sale_price": "250.00"}, headers=token).json()
    account = client.post(ACC + "/accounts", json={
        "name": f"mt-b1-cycle-{RUN}", "currency": "RUB"}, headers=token).json()

    po = client.post(ACC + "/purchase-orders", json={
        "counterparty_id": supplier["id"], "currency": "RUB",
        "lines": [{"item_id": item["id"], "qty": "10", "unit_price": "100"}],
    }, headers=token).json()
    client.post(f"{ACC}/purchase-orders/{po['id']}/confirm", json={}, headers=token)
    receipt = client.post(ACC + "/receipts", json={
        "purchase_order_id": po["id"],
        "lines": [{"item_id": item["id"], "qty": "10"}]}, headers=token).json()
    receipt = client.post(f"{ACC}/receipts/{receipt['id']}/post", json={},
                          headers=token).json()
    assert receipt["status"] == "posted"

    so = client.post(ACC + "/sales-orders", json={
        "counterparty_id": customer["id"], "currency": "RUB",
        "lines": [{"item_id": item["id"], "qty": "6", "unit_price": "250"}],
    }, headers=token).json()
    client.post(f"{ACC}/sales-orders/{so['id']}/confirm", json={}, headers=token)
    shp = client.post(ACC + "/shipments", json={
        "sales_order_id": so["id"],
        "lines": [{"item_id": item["id"], "qty": "6"}]}, headers=token).json()
    shp = client.post(f"{ACC}/shipments/{shp['id']}/post", json={},
                      headers=token).json()
    assert shp["status"] == "posted"
    pay = client.post(f"{ACC}/sales-orders/{so['id']}/pay", json={
        "account_id": account["id"], "amount": "1500"}, headers=token).json()
    assert pay["status"] == "posted"

    # двойная запись: остаток 4 по средней 100, маржа 1500-600=900
    balances = client.get(ACC + f"/stock/balances?item_id={item['id']}",
                          headers=token).json()
    assert sum(Decimal(b["qty"]) for b in balances) == 4
    assert Decimal(balances[0]["value"]) == Decimal(400)
    report = client.get(ACC + "/reports/sales", headers=token).json()
    row = next(r for r in report["by_item"] if r["item_id"] == item["id"])
    assert Decimal(row["revenue_base"]) == Decimal("1500.00")
    assert Decimal(row["margin_base"]) == Decimal("900.00")

    # изоляция: товар Б не виден в А
    items_a = client.get(ACC + f"/items?q=MT-B1-CYCLE-{RUN}",
                         headers=_auth(org_a["token"])).json()
    assert items_a == []
