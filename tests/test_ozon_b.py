"""Ozon Seller — этап B (ozon-connector-spec §7.3–7.5): заказы → ERP.

Мок Ozon с изменяемым состоянием (заказы): sync orders → ozon_orders;
auto_create_orders → sales_orders draft (контрагент «Ozon», строки по
item_mappings, Decimal-строки); несмапленная позиция → mapping_error,
после маппинга и ретрая заказ создаётся; отмена на Ozon → cancelled,
черновик удаляется.

Запуск: docker compose exec api pytest tests/test_ozon_b.py
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration

OFFER_A = f"OZB-{RUN}-A"
OFFER_B = f"OZB-{RUN}-B"

# posting_number → заказ (мок отдаёт состояние модуля)
# формат Ozon: products[] с quantity/price (коннектор нормализует в lines)
ORDERS: dict[str, dict] = {
    "0001": {"posting_number": "0001", "status": "delivered",
             "order_date": "2026-09-24T10:00:00Z", "amount": "2490.00",
             "products": [
                 {"offer_id": OFFER_A, "quantity": 1, "price": "1500.00"},
                 {"offer_id": OFFER_B, "quantity": 1, "price": "990.00"}]},
}


class _OzonMock(BaseHTTPRequestHandler):
    def _reply(self, payload: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/v5/order/list":
            self._reply({"result": {"postings": list(ORDERS.values())}})
        else:
            self._reply({"result": {}})

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def ozon_mock():
    server = HTTPServer(("127.0.0.1", 0), _OzonMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()
    server.server_close()


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


def _admin(client):
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"],
        "company_id": login["organizations"][0]["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


def _connection(client, ozon_mock, headers, auto="on"):
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ozon-b-{RUN}", "connector_code": "ozon_seller",
        "credentials": {"client_id": "c", "api_key": "k"},
        "config": {"base_url": ozon_mock, "auto_create_orders": auto},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    return conn.json()


def _run(kind: str, connection) -> dict:
    from src.db import SessionLocal
    from src.modules.integrations.connectors.builtin import registry
    from src.modules.integrations.crypto import decrypt_dict
    from src.modules.integrations.ozon import run_ozon_sync

    connector = registry.build("ozon_seller", connection.config,
                               decrypt_dict(connection.credentials_enc))
    db = SessionLocal()
    try:
        return run_ozon_sync(db, kind=kind, connection=connection,
                             connector=connector)
    finally:
        db.close()


def _get(conn_id):
    from src.db import SessionLocal
    from src.modules.integrations import models as im

    db = SessionLocal()
    try:
        return db.get(im.Connection, conn_id)
    finally:
        db.close()


def test_stage_b_orders_to_erp(client, ozon_mock):
    headers = _admin(client)
    conn = _connection(client, ozon_mock, headers)

    # номенклатура под маппинг (2 позиции)
    item_a = client.post(f"{API}/accounting/items", json={
        "sku": OFFER_A, "name": f"Ozon A {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()
    item_b = client.post(f"{API}/accounting/items", json={
        "sku": OFFER_B, "name": f"Ozon B {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()

    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from sqlalchemy import select

    db = SessionLocal()

    def ozon_row(posting):
        return db.scalar(select(im.OzonOrder).where(
            im.OzonOrder.connection_id == conn["id"],
            im.OzonOrder.posting_number == posting))

    def clear_mappings():
        for row in db.scalars(select(im.ItemMapping).where(
                im.ItemMapping.connection_id == conn["id"])).all():
            db.delete(row)
        db.commit()

    def map_offer(offer, item_id):
        resp = client.post(f"{API}/integrations/item-mappings", json={
            "connection_id": conn["id"], "external_item_id": offer,
            "sku": offer, "item_id": item_id}, headers=headers)
        assert resp.status_code == 201, resp.text

    try:
        connection = _get(conn["id"])

        # §7.4: только A смаплен → mapping_error, заказа нет
        clear_mappings()
        map_offer(OFFER_A, item_a["id"])
        r1 = _run("orders", connection)
        assert r1["ok"] and r1["new"] == 1 and r1["mapping_errors"] == 1
        row = ozon_row("0001")
        assert row.sales_order_id is None and row.mapping_error is True

        # повторный sync без изменений — дубля ozon_orders нет
        r2 = _run("orders", connection)
        assert r2["new"] == 0
        total = len(db.scalars(select(im.OzonOrder).where(
            im.OzonOrder.connection_id == conn["id"])).all())
        assert total == 1

        # §7.4-ретрай: доложили маппинг B → заказ создан, флаг снят
        map_offer(OFFER_B, item_b["id"])
        r3 = _run("orders", connection)
        assert r3["sales_orders"] == 1 and r3["mapping_errors"] == 0
        db.refresh(row)
        assert row.sales_order_id is not None and row.mapping_error is False

        # §7.3: draft, контрагент «Ozon», строки/цены из заказа
        order = client.get(f"{API}/accounting/sales-orders/{row.sales_order_id}",
                           headers=headers).json()
        assert order["status"] == "draft"
        counterparty = client.get(
            f"{API}/accounting/counterparties?q=Ozon&limit=20", headers=headers
        ).json()
        cp_ids = {c["id"] for c in counterparty["items"]} if isinstance(counterparty, dict) else {c["id"] for c in counterparty}
        assert order["counterparty_id"] in cp_ids
        lines = {l["item_id"]: l for l in order["lines"]}
        from decimal import Decimal as _D
        assert _D(str(lines[item_a["id"]]["unit_price"])) == _D("1500.00")
        assert _D(str(lines[item_b["id"]]["qty"])) == _D("1")

        # §7.5: отмена на Ozon → cancelled, черновик удалён
        ORDERS["0001"]["status"] = "cancelled"
        r4 = _run("orders", connection)
        assert r4["cancelled"] == 1
        db.refresh(row)
        assert row.status == "cancelled"
        assert row.sales_order_id is None
        gone = client.get(f"{API}/accounting/sales-orders/{order['id']}",
                          headers=headers)
        assert gone.status_code == 404
    finally:
        db.close()
