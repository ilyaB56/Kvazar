"""shipment_lines.serial_ids: коды-активы не хранятся открыто (пост-гейт).

rw (admin) видит расшифрованные serial_codes в GET отгрузки; readonly —
только отпечатки (serial_fingerprints) и факт количества. FIFO-выдача
фиксируется serial_ids; явные коды при создании резолвятся в ids.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
ACC = "/api/v1/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]
run_tag = RUN


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
def readonly_headers(client, admin_headers):
    client.post("/api/v1/users", json={
        "email": "readonly.serials-test@erp.local",
        "password": "readonly1pass", "role": "readonly",
    }, headers=admin_headers)  # 409 на дубль — не важно
    return _login(client, "readonly.serials-test@erp.local", "readonly1pass")


@pytest.fixture(scope="module")
def posted_shipment(client, admin_headers):
    """Цифровой товар 2 кода → заказ → отгрузка FIFO → post."""
    cp = client.post(f"{ACC}/counterparties", json={"name": f"serials-rw-{RUN}"},
                     headers=admin_headers).json()
    item = client.post(f"{ACC}/items", json={
        "sku": f"SERIDS-{RUN}", "name": f"serial ids {RUN}", "kind": "digital",
        "unit_code": "лицензия", "tracking": "serial",
    }, headers=admin_headers).json()
    loc = next(row for row in client.get(f"{ACC}/locations", headers=admin_headers).json()
               if row["name"] == "Цифровой склад")
    codes = [f"SERIDS-{RUN}-{i}" for i in (1, 2)]
    receipt = client.post(f"{ACC}/receipts", json={
        "counterparty_id": cp["id"],
        "lines": [{"item_id": item["id"], "qty": "2", "unit_cost": "100",
                   "location_id": loc["id"], "serial_codes": codes}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{receipt['id']}/post", headers=admin_headers)

    order = client.post(f"{ACC}/sales-orders", json={
        "counterparty_id": cp["id"],
        "lines": [{"item_id": item["id"], "qty": "2", "unit_price": "200"}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/sales-orders/{order['id']}/confirm", headers=admin_headers)
    ship = client.post(f"{ACC}/shipments", json={
        "sales_order_id": order["id"],
        "lines": [{"item_id": item["id"], "qty": "2", "location_id": loc["id"]}],
    }, headers=admin_headers).json()
    return client.post(f"{ACC}/shipments/{ship['id']}/post", headers=admin_headers).json()


def test_rw_sees_codes_and_ids(client, admin_headers, posted_shipment):
    line = posted_shipment["lines"][0]
    assert line["serial_ids"], "serial_ids missing (FIFO not fixed?)"
    assert len(line["serial_ids"]) == 2
    assert sorted(line["serial_codes"]) == sorted(
        [f"SERIDS-{RUN}-1", f"SERIDS-{RUN}-2"]), "rw must see decrypted codes"
    # повторный GET — то же самое
    got = client.get(f"{ACC}/shipments/{posted_shipment['id']}", headers=admin_headers).json()
    assert got["lines"][0]["serial_codes"] == line["serial_codes"]
    assert got["lines"][0]["serial_ids"] == line["serial_ids"]


def test_readonly_sees_fingerprints_only(client, readonly_headers, posted_shipment):
    got = client.get(f"{ACC}/shipments/{posted_shipment['id']}", headers=readonly_headers)
    assert got.status_code == 200
    line = got.json()["lines"][0]
    assert line["serial_codes"] is None, "readonly must NOT see decrypted codes"
    assert line["serial_fingerprints"], "readonly must see fingerprints"
    assert len(line["serial_fingerprints"]) == 2
    assert all(len(fp) == 8 for fp in line["serial_fingerprints"])
    assert line["serial_ids"], "ids (ссылки) readonly видит"


def test_open_codes_not_stored_in_lines(client, admin_headers, posted_shipment):
    """В БД строка хранит только UUID — открытых кодов нет (grep по JSONB)."""
    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        raw = db.execute(text(
            "SELECT serial_codes FROM mgmt_accounting.shipment_lines "
            "WHERE id = :lid"), {"lid": posted_shipment["lines"][0]["id"]}).scalar()
        assert raw is None, f"legacy serial_codes must stay NULL after migration, got {raw}"
    finally:
        db.close()
