"""Ozon Seller — этап C (ozon-connector-spec §7.6–7.7, 7.11).

Транзакции Ozon → расходы по статьям («Комиссия Ozon»/«Логистика Ozon»/
«Реклама Ozon»), идемпотентность (UNIQUE operation_id), push остатков,
отчёт маржи (выручка − комиссии − себестоимость, флаг include_cost).

Запуск: docker compose exec api pytest tests/test_ozon_c.py
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

OFFER = f"OZC-{RUN}"

TRANSACTIONS = [
    {"operation_id": f"{RUN}-1", "operation_type": "MarketplaceCommission",
     "amount": "-150.00", "date": "2026-09-20", "items": []},
    {"operation_id": f"{RUN}-2", "operation_type": "Логистика last mile",
     "amount": "-80.50", "date": "2026-09-20", "items": []},
    {"operation_id": f"{RUN}-3", "operation_type": "Реклама трафареты",
     "amount": "-30.00", "date": "2026-09-21", "items": []},
    # transfer не проводим (§10.2)
    {"operation_id": f"{RUN}-4", "operation_type": "Transfer",
     "amount": "5000.00", "date": "2026-09-21", "items": []},
]
DELIVERED_ORDER = {"posting_number": f"C{RUN}", "status": "delivered",
                   "order_date": "2026-09-20T12:00:00Z", "amount": "1000.00",
                   "products": [{"offer_id": OFFER, "quantity": 2,
                                 "price": "500.00"}]}
PUSHED: list[dict] = []


class _OzonMock(BaseHTTPRequestHandler):
    def _reply(self, payload: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/v1/report/transactions":
            self._reply({"result": {"operations": TRANSACTIONS}})
        elif path == "/v5/order/list":
            self._reply({"result": {"postings": [DELIVERED_ORDER]}})
        elif path == "/v1/product/import/stocks":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            PUSHED.append(body)
            self._reply({"result": []})
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


def test_stage_c_expenses_margin_push(client, ozon_mock):
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ozon-c-{RUN}", "connector_code": "ozon_seller",
        "credentials": {"client_id": "c", "api_key": "k"},
        "config": {"base_url": ozon_mock, "auto_create_orders": "on"},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    cid = conn.json()["id"]

    # номенклатура + маппинг + склад 10 шт (для push и себестоимости)
    item = client.post(f"{API}/accounting/items", json={
        "sku": OFFER, "name": f"C {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()
    client.post(f"{API}/integrations/item-mappings", json={
        "connection_id": cid, "external_item_id": OFFER, "sku": OFFER,
        "item_id": item["id"]}, headers=headers)
    from src.db import SessionLocal
    from src.modules.mgmt_accounting.features.inventory import models as inv_m
    from sqlalchemy import select as _sel

    from src.core.models import Company

    _db = SessionLocal()
    try:
        _org = _db.scalar(_sel(Company).where(Company.name == "Основная"))
        _loc = _db.scalar(_sel(inv_m.Location).where(
            inv_m.Location.company_id == _org.id,
            inv_m.Location.is_active.is_(True),
            inv_m.Location.kind != "transit").limit(1))
        loc = {"id": str(_loc.id)}
    finally:
        _db.close()
    cp = client.post(f"{API}/accounting/counterparties", json={
        "name": f"c-поставщик-{RUN}"}, headers=headers).json()
    po = client.post(f"{API}/accounting/purchase-orders", json={
        "counterparty_id": cp["id"], "currency": "RUB",
        "lines": [{"item_id": item["id"], "qty": "10", "unit_price": "100"}]},
        headers=headers).json()
    client.post(f"{API}/accounting/purchase-orders/{po['id']}/confirm",
                headers=headers)
    rc_resp = client.post(f"{API}/accounting/receipts", json={
        "purchase_order_id": po["id"], "lines": [
            {"item_id": item["id"], "qty": "10", "unit_cost": "100",
            "location_id": loc["id"]}]}, headers=headers)
    assert rc_resp.status_code == 201, rc_resp.text
    rc = rc_resp.json()
    client.post(f"{API}/accounting/receipts/{rc['id']}/post", headers=headers)

    # заказ (delivered) + транзакции → POST /ozon/sync
    sync = client.post(f"{API}/integrations/ozon/sync", json={
        "connection_id": cid, "kinds": ["orders", "transactions"]},
        headers=headers)
    assert sync.status_code == 200, sync.text

    # §7.6: расходы по статьям созданы; transfer не проведён
    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from sqlalchemy import select

    db = SessionLocal()
    try:
        txns = db.scalars(select(im.OzonTransaction).where(
            im.OzonTransaction.connection_id == cid)).all()
        by_op = {t.operation_id: t for t in txns}
        assert len(txns) == 4
        assert by_op[f"{RUN}-1"].transaction_id is not None
        assert by_op[f"{RUN}-2"].transaction_id is not None
        assert by_op[f"{RUN}-3"].transaction_id is not None
        assert by_op[f"{RUN}-4"].transaction_id is None  # transfer

        # повторный sync — без дублей (UNIQUE operation_id)
        sync2 = client.post(f"{API}/integrations/ozon/sync", json={
            "connection_id": cid, "kinds": ["transactions"]}, headers=headers)
        assert sync2.status_code == 200
        assert len(db.scalars(select(im.OzonTransaction).where(
            im.OzonTransaction.connection_id == cid)).all()) == 4
        db.expire_all()
    finally:
        db.close()

    # статьи создались в справочнике
    cats = client.get(f"{API}/accounting/categories?limit=200", headers=headers).json()
    cats = cats["items"] if isinstance(cats, dict) else cats
    names = {c["name"] for c in cats}
    assert {"Комиссия Ozon", "Логистика Ozon", "Реклама Ozon"} <= names

    # §7.11: маржа — выручка 1000 − (150+80.50+30) − себестоимость (2×100)
    margin = client.get(f"{API}/integrations/ozon/margin", params={
        "connection_id": cid, "date_from": "2026-09-01",
        "date_to": "2026-09-30"}, headers=headers).json()
    from decimal import Decimal as D
    assert D(margin["revenue"]) == D("1000.00")
    assert D(margin["fees"]) == D("150.00")
    assert D(margin["logistics"]) == D("80.50")
    assert D(margin["advertising"]) == D("30.00")
    assert D(margin["cost"]) == 0 and margin["include_cost"] is False
    assert D(margin["profit"]) == D("739.50")

    margin_cost = client.get(f"{API}/integrations/ozon/margin", params={
        "connection_id": cid, "date_from": "2026-09-01",
        "date_to": "2026-09-30", "include_cost": "true"}, headers=headers).json()
    assert D(margin_cost["cost"]) == D("200.00")  # 2 шт × avg_cost 100
    assert D(margin_cost["profit"]) == D("539.50")

    # §7.7: push остатков — в мок ушёл qty=10 (по смапленному товару)
    push = client.post(f"{API}/integrations/ozon/push-stocks", json={
        "connection_id": cid, "kinds": ["stocks"]}, headers=headers)
    assert push.status_code == 200, push.text
    assert push.json()["pushed"] == 1
    assert PUSHED, "в мок не ушёл POST /v1/product/import/stocks"
    stock = PUSHED[-1]["stocks"][0]
    assert stock["offer_id"] == OFFER and stock["stock"] == 10
