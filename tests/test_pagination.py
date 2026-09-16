"""Пагинация списков: limit/offset/total на всех list-эндпоинтах.

Совместимость: без параметров — прежний массив (тесты/фронт не ломаются);
?limit=N&offset=M / ?limit=0 / ?format=paginated — конверт {items, total}.

Проверяем представителей каждого паттерна серверного кода:
- page.apply по ORM-запросу (transactions, items, deals)
- transform/пост-обработка строк (webhooks, auth/sessions, payments)
- пагинация в БД у журнала движений (stock/moves)
- платформенный реестр организаций (platform/orgs)

Запуск: docker compose exec api pytest tests/test_pagination.py
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=60,
                        headers={"User-Agent": "pagination-test"})
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


@pytest.fixture(scope="module")
def org_token(client):
    """Админ «Основной» с выбранным контекстом организации."""
    response = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")})
    assert response.status_code == 200
    data = response.json()
    orgs = data.get("organizations") or []
    assert orgs, "нет организаций у админа"
    selected = client.post(f"{API}/auth/select-org", json={
        "refresh_token": data["refresh_token"],
        "company_id": orgs[0]["id"]})
    assert selected.status_code == 200
    return {"Authorization": f"Bearer {selected.json()['access_token']}"}


@pytest.fixture(scope="module")
def pl_token(client):
    """Платформенный контекст (без select-org)."""
    response = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_legacy_format_is_plain_list(client, org_token):
    """Без параметров — прежний массив (обратная совместимость)."""
    for path in (f"{API}/accounting/transactions", f"{API}/accounting/items",
                 f"{API}/crm/deals", f"{API}/users"):
        response = client.get(path, headers=org_token)
        assert response.status_code == 200, path
        assert isinstance(response.json(), list), path


def test_paginated_envelope_and_offsets(client, org_token):
    """limit/offset → {items, total}; страницы не перекрываются, total
    стабилен и равен числу строк при limit=0."""
    response = client.get(f"{API}/accounting/transactions?limit=2",
                          headers=org_token)
    assert response.status_code == 200
    page1 = response.json()
    assert set(page1) == {"items", "total"}
    assert len(page1["items"]) == 2
    total = page1["total"]
    assert isinstance(total, int) and total >= 2

    page2 = client.get(f"{API}/accounting/transactions?limit=2&offset=2",
                       headers=org_token).json()
    assert page2["total"] == total
    ids1 = {row["id"] for row in page1["items"]}
    ids2 = {row["id"] for row in page2["items"]}
    assert not ids1 & ids2, "offset=2 не сместил выборку"

    everything = client.get(f"{API}/accounting/transactions?limit=0",
                            headers=org_token).json()
    assert everything["total"] == total
    assert len(everything["items"]) == total


def test_format_paginated_without_limit(client, org_token):
    """?format=paginated без limit — конверт со всеми строками."""
    plain = client.get(f"{API}/accounting/items", headers=org_token).json()
    wrapped = client.get(f"{API}/accounting/items?format=paginated",
                         headers=org_token).json()
    assert isinstance(plain, list)
    assert wrapped["total"] == len(plain)
    assert len(wrapped["items"]) == len(plain)


def test_representatives_of_each_pattern(client, org_token):
    """Пост-обработка строк (Out-схемы/маски) не ломается в конверте."""
    cases = [
        f"{API}/accounting/stock/moves",   # пагинация в БД
        f"{API}/accounting/stock/balances",  # агрегация, срез пост-фактум
        f"{API}/accounting/purchase-orders",  # _order_with_lines
        f"{API}/accounting/sales-orders",
        f"{API}/accounting/shipments",
        f"{API}/accounting/receipts",
        f"{API}/accounting/counterparties",
        f"{API}/accounting/accounts",
        f"{API}/accounting/categories",
        f"{API}/accounting/locations",
        f"{API}/crm/deals",                # _enrich поверх строк
        f"{API}/crm/activities",
        f"{API}/integrations/connections",
        f"{API}/integrations/webhooks",    # transform=WebhookOut(...)
        f"{API}/integrations/payments",    # маска покупателя для ro/rw
        f"{API}/integrations/item-mappings",
        f"{API}/integrations/notification-rules",
        f"{API}/ai/documents",
        f"{API}/ai/proposals",
        f"{API}/ai/sessions",
        f"{API}/auth/sessions",            # transform=AuthSessionOut(...)
    ]
    for path in cases:
        response = client.get(f"{path}?limit=2", headers=org_token)
        assert response.status_code == 200, path
        body = response.json()
        assert isinstance(body, dict) and set(body) == {"items", "total"}, path
        assert len(body["items"]) <= 2, path
        assert isinstance(body["total"], int) and body["total"] >= 0, path


def test_platform_orgs_paginated(client, pl_token):
    """Реестр организаций платформы: конверт + срез по offset."""
    legacy = client.get(f"{API}/platform/orgs", headers=pl_token)
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    page = client.get(f"{API}/platform/orgs?limit=2&offset=1",
                      headers=pl_token).json()
    assert set(page) == {"items", "total"}
    assert len(page["items"]) <= 2
    full = client.get(f"{API}/platform/orgs?limit=0",
                      headers=pl_token).json()
    assert full["total"] == len(full["items"])
    if full["total"] > 3:
        assert page["items"][0]["id"] != full["items"][0]["id"]


def test_validation_errors(client, org_token):
    """limit<0 / offset<0 / format=мусор → 422, а не 500."""
    for query in ("limit=-1", "offset=-5", "format=array", "limit=abc"):
        response = client.get(f"{API}/accounting/transactions?{query}",
                              headers=org_token)
        assert response.status_code == 422, query


def test_filters_counted_in_total(client, org_token):
    """total считается по отфильтрованному запросу, а не по всей таблице."""
    items = client.get(f"{API}/accounting/items?limit=0",
                       headers=org_token).json()
    if not items["items"]:
        pytest.skip("нет номенклатуры для фильтра")
    sku = items["items"][0]["sku"]
    filtered = client.get(f"{API}/accounting/items?q={sku}",
                          headers=org_token).json()
    assert isinstance(filtered, list) and filtered, "фильтр по sku пуст"
    wrapped = client.get(f"{API}/accounting/items?q={sku}&limit=1",
                         headers=org_token).json()
    assert wrapped["total"] == len(filtered)
