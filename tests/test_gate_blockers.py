"""Гейт 1.2 — блокеры аналитика (pilots-readiness §1.2).

Д6: конкурентное проведение двух отгрузок одного остатка — вторая
получает insufficient_stock (advisory-lock по item×location).
Д7: FIFO-выданные коды фиксируются в строке отгрузки при проведении.
П10: себестоимость выдачи цифровых = unit_cost прихода конкретного кода.
Д13: команда «испортить код» — движение в «Брак» + status=void;
транзитные локации не участвуют в kind-совместимости.

Интеграционные тесты против запущенного API (docker compose up).
"""

from __future__ import annotations

import os
import threading
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, "admin@example.com", os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"))


@pytest.fixture(scope="module")
def stage(client, admin_headers):
    """Дано: цифровой товар с 4 кодами (по 200) на цифровом складе +
    заказ клиента на 4 шт (позднее две отгрузки по 4 = гонка Д6)."""
    run = uuid.uuid4().hex[:8]
    cp = client.post("/api/v1/accounting/counterparties",
                     json={"name": f"pytest-D6-клиент-{run}"}, headers=admin_headers)
    cp_id = cp.json()["id"]
    item = client.post("/api/v1/accounting/items", json={
        "sku": f"D6-{run}", "name": f"pytest D6 digital {run}",
        "kind": "digital", "unit_code": "лицензия", "tracking": "serial",
    }, headers=admin_headers).json()
    loc = client.post("/api/v1/accounting/locations", json={
        "name": f"pytest-D6-склад-{run}", "kind": "digital",
    }, headers=admin_headers).json()
    codes = [f"D6-{run}-{i}" for i in range(4)]
    receipt = client.post("/api/v1/accounting/receipts", json={
        "counterparty_id": cp_id,
        "lines": [{"item_id": item["id"], "qty": "4", "unit_cost": "200",
                   "location_id": loc["id"], "serial_codes": codes}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/receipts/{receipt['id']}/post", headers=admin_headers)
    order = client.post("/api/v1/accounting/sales-orders", json={
        "counterparty_id": cp_id,
        "lines": [{"item_id": item["id"], "qty": "4", "unit_price": "500"}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/sales-orders/{order['id']}/confirm", headers=admin_headers)
    return {"run": run, "cp": cp_id, "item": item, "loc": loc,
            "codes": codes, "order": order}


def _make_shipment(client, admin_headers, stage, qty: str) -> dict:
    return client.post("/api/v1/accounting/shipments", json={
        "sales_order_id": stage["order"]["id"],
        "lines": [{"item_id": stage["item"]["id"], "qty": qty,
                   "location_id": stage["loc"]["id"]}],
    }, headers=admin_headers).json()


def test_d6_concurrent_post_one_wins(client, admin_headers, stage):
    """Две отгрузки по 4 (в наличии ровно 4): конкурентный post — ровно
    одна проводится, вторая получает 422 insufficient_stock (или 409
    over_shipment по остатку заказа) — но НЕ обе успешны."""
    ship1 = _make_shipment(client, admin_headers, stage, "4")
    ship2 = _make_shipment(client, admin_headers, stage, "4")

    results: list[int] = []

    def post(shipment_id: str):
        response = client.post(
            f"/api/v1/accounting/shipments/{shipment_id}/post", headers=admin_headers)
        results.append(response.status_code)

    threads = [threading.Thread(target=post, args=(s["id"],))
               for s in (ship1, ship2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 422], f"expected one 200 + one 422, got {results}"
    # на складе не осталось двойной выдачи: баланс 0, sold-серийников ровно 4
    balances = client.get(
        "/api/v1/accounting/stock/balances",
        params={"item_id": stage["item"]["id"], "location_id": stage["loc"]["id"]},
        headers=admin_headers).json()
    assert not balances, f"digital stock leaked: {balances}"


def test_d7_fifo_codes_fixed_in_line(client, admin_headers, stage):
    """FIFO-выдача фиксируется в shipment_lines: повторный GET отдаёт те же
    коды, что вернул post."""
    order2 = client.post("/api/v1/accounting/sales-orders", json={
        "counterparty_id": stage["cp"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "2", "unit_price": "500"}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/sales-orders/{order2['id']}/confirm", headers=admin_headers)
    # пополняем: 2 кода по 300 (для П10 — другая партия, другая себестоимость)
    receipt = client.post("/api/v1/accounting/receipts", json={
        "counterparty_id": stage["cp"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "2", "unit_cost": "300",
                   "location_id": stage["loc"]["id"],
                   "serial_codes": [f"D6-{stage['run']}-A1", f"D6-{stage['run']}-A2"]}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/receipts/{receipt['id']}/post", headers=admin_headers)

    ship = client.post("/api/v1/accounting/shipments", json={
        "sales_order_id": order2["id"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "2",
                   "location_id": stage["loc"]["id"]}],
    }, headers=admin_headers).json()
    posted = client.post(
        f"/api/v1/accounting/shipments/{ship['id']}/post", headers=admin_headers).json()
    line = posted["lines"][0]
    assert line.get("serial_codes"), "FIFO codes not fixed into the line (Д7)"
    assert len(line["serial_codes"]) == 2
    # повторный GET — те же коды
    got = client.get(f"/api/v1/accounting/shipments/{ship['id']}",
                     headers=admin_headers).json()
    assert got["lines"][0]["serial_codes"] == line["serial_codes"]


def test_p10_serial_cogs_is_code_unit_cost(client, admin_headers, stage):
    """Себестоимость выдачи = unit_cost прихода конкретных кодов.
    Все партии по 200 и 300 выданы — движение отгрузки несёт среднюю по
    кодам партии, не avg_cost товара (вперемешку быть не может: FIFO)."""
    moves = client.get("/api/v1/accounting/stock/moves", params={
        "item_id": stage["item"]["id"]}, headers=admin_headers).json()
    shipment_moves = [m for m in moves if m["source_type"] == "shipment"]
    assert shipment_moves, "no shipment moves found"
    costs = {m["unit_cost"] for m in shipment_moves}
    # первая отгрузка — 4 кода по 200 (COGS 200), вторая — 2 кода по 300 (300)
    assert "200.0000" in costs, f"first batch cost missing: {costs}"
    assert "300.0000" in costs, f"second batch cost missing: {costs}"


def test_d13_void_code(client, admin_headers, stage):
    """«Испортить код»: движение в транзит «Брак» + status=void; повторная
    порча того же кода — 404; kind-совместимость на транзит не ругается."""
    order3 = client.post("/api/v1/accounting/sales-orders", json={
        "counterparty_id": stage["cp"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "1", "unit_price": "500"}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/sales-orders/{order3['id']}/confirm", headers=admin_headers)
    receipt = client.post("/api/v1/accounting/receipts", json={
        "counterparty_id": stage["cp"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "1", "unit_cost": "250",
                   "location_id": stage["loc"]["id"],
                   "serial_codes": [f"D6-{stage['run']}-V1"]}],
    }, headers=admin_headers).json()
    client.post(f"/api/v1/accounting/receipts/{receipt['id']}/post", headers=admin_headers)

    voided = client.post("/api/v1/accounting/serials/void", json={
        "code": f"D6-{stage['run']}-V1", "note": "pytest порча",
    }, headers=admin_headers)
    assert voided.status_code == 201, voided.text
    move = voided.json()
    assert move["source_type"] == "serial_void"

    # повторная порча — код уже void
    again = client.post("/api/v1/accounting/serials/void", json={
        "code": f"D6-{stage['run']}-V1"}, headers=admin_headers)
    assert again.status_code == 404

    # неизвестный код — 404
    unknown = client.post("/api/v1/accounting/serials/void", json={
        "code": "NO-SUCH-CODE-XYZ"}, headers=admin_headers)
    assert unknown.status_code == 404
