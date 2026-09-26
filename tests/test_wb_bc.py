"""Wildberries — этапы B+C (wb-connector-spec §7.3–7.12).

Заказы → sales_orders draft (контрагент «WB», vendor_code-маппинг,
mapping_error → ретрай; отмена); комиссии по статьям «WB: …» с
fallback «WB: Прочее», payment без расхода; маржа; push остатков по
привязанной локации (config.warehouses).

Запуск: docker compose exec api pytest tests/test_wb_bc.py
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration

VC_A = f"WBC-{RUN}-A"
VC_B = f"WBC-{RUN}-B"

ORDERS = [
    {"uid": "W1", "status": "delivered", "date": "2026-09-26T10:00:00Z",
     "price": "1490.00", "nmId": 201, "article": VC_A, "quantity": 1},
    {"uid": "W2", "status": "delivered", "date": "2026-09-26T11:00:00Z",
     "price": "700.00", "nmId": 202, "article": VC_B, "quantity": 1},
]
TRANSACTIONS = [
    {"operationId": f"{RUN}-c", "operationType": "Комиссия",
     "price": "-180.00", "date": "2026-09-26"},
    {"operationId": f"{RUN}-l", "operationType": "Логистика",
     "price": "-90.25", "date": "2026-09-26"},
    {"operationId": f"{RUN}-s", "operationType": "Хранение",
     "price": "-15.00", "date": "2026-09-26"},
    {"operationId": f"{RUN}-p", "operationType": "Штраф",
     "price": "-50.00", "date": "2026-09-26"},
    {"operationId": f"{RUN}-t", "operationType": "Налог",
     "price": "-30.00", "date": "2026-09-26"},
    {"operationId": f"{RUN}-x", "operationType": "Вознаграждение нигде",
     "price": "-10.00", "date": "2026-09-26"},
    # выплата — записывается, денег НЕ проводим (§10.2 / §7.7)
    {"operationId": f"{RUN}-pay", "operationType": "Оплата продавцу",
     "price": "2000.00", "date": "2026-09-26"},
]
PUSHED: list[tuple] = []


class _WBMock(BaseHTTPRequestHandler):
    def _reply(self, payload, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _handle(self):
        if self.headers.get("Authorization") != "t":
            self._reply({"error": "unauthorized"}, status=403)
            return
        path = self.path.split("?")[0]
        if path == "/api/v3/orders":
            self._reply(ORDERS)
        elif path == "/finance/v1/transactions":
            self._reply({"operations": TRANSACTIONS})
        elif path.startswith("/api/v3/stocks/"):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            PUSHED.append((path.split("/")[-1], body))
            self._reply({})
        else:
            self._reply({})

    def do_GET(self):  # noqa: N802
        self._handle()

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._handle()

    def do_PUT(self):  # noqa: N802
        self._handle()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def wb_mock():
    server = HTTPServer(("127.0.0.1", 0), _WBMock)
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


def test_wb_stage_b_orders(client, wb_mock):
    headers = _admin(client)
    item_a = client.post(f"{API}/accounting/items", json={
        "sku": VC_A, "name": f"WB A {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()
    item_b = client.post(f"{API}/accounting/items", json={
        "sku": VC_B, "name": f"WB B {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()

    from src.core.models import Company
    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from src.modules.mgmt_accounting.features.inventory import models as inv_m
    from sqlalchemy import select

    db = SessionLocal()
    org = db.scalar(select(Company).where(Company.name == "Основная"))
    loc = db.scalar(select(inv_m.Location).where(
        inv_m.Location.company_id == org.id,
        inv_m.Location.is_active.is_(True),
        inv_m.Location.kind != "transit").limit(1))

    # connection с привязкой склада WB → наша локация (§10.1)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"wbc-{RUN}", "connector_code": "wb_seller",
        "credentials": {"api_token": "t"},
        "config": {"base_url": wb_mock, "auto_create_orders": "on",
                   "warehouses": json.dumps({"wh-main": str(loc.id)})},
    }, headers=headers).json()
    cid = conn["id"]

    def map_vc(vc, item_id):
        resp = client.post(f"{API}/integrations/item-mappings", json={
            "connection_id": cid, "external_item_id": vc, "sku": vc,
            "item_id": item_id}, headers=headers)
        assert resp.status_code == 201, resp.text

    def wb_row(uid):
        return db.scalar(select(im.WBOrder).where(
            im.WBOrder.connection_id == cid, im.WBOrder.uid == uid))

    try:
        # §7.4: только A смаплен → mapping_error, заказа нет
        map_vc(VC_A, item_a["id"])
        r1 = client.post(f"{API}/integrations/wb/sync", json={
            "connection_id": cid, "kinds": ["orders"]}, headers=headers)
        assert r1.status_code == 200, r1.text
        assert r1.json()["orders"]["mapping_errors"] >= 1
        row = wb_row("W2")  # W2 — несмапленная позиция
        assert row.sales_order_id is None and row.mapping_error is True
        w1 = wb_row("W1")  # W1 смаплена — создан сразу (§7.3)
        assert w1.sales_order_id is not None

        # ретрай после маппинга B → W2 создан, флаг снят (§7.4)
        map_vc(VC_B, item_b["id"])
        r2 = client.post(f"{API}/integrations/wb/sync", json={
            "connection_id": cid, "kinds": ["orders"]}, headers=headers)
        assert r2.status_code == 200
        db.refresh(row)
        assert row.sales_order_id is not None and row.mapping_error is False
        order = client.get(
            f"{API}/accounting/sales-orders/{row.sales_order_id}",
            headers=headers).json()
        assert order["status"] == "draft"
        from src.modules.mgmt_accounting import models as acc_m

        cp = db.get(acc_m.Counterparty, order["counterparty_id"])
        assert cp.name == "WB"
        lines = {l["item_id"]: l for l in order["lines"]}
        assert D(str(lines[item_b["id"]]["unit_price"])) == D("700.00")
        w1_order = client.get(
            f"{API}/accounting/sales-orders/{w1.sales_order_id}",
            headers=headers).json()
        assert D(str({l["item_id"]: l for l in w1_order["lines"]}[item_a["id"]]["unit_price"])) == D("1490.00")

        # §7.5: отмена на WB → cancelled, черновик удалён
        for o in ORDERS:
            o["isCancel"] = True
        r3 = client.post(f"{API}/integrations/wb/sync", json={
            "connection_id": cid, "kinds": ["orders"]}, headers=headers)
        assert r3.status_code == 200
        assert r3.json()["orders"]["cancelled"] >= 1
        db.refresh(row)
        assert row.status == "cancelled"
        gone = client.get(f"{API}/accounting/sales-orders/{order['id']}",
                          headers=headers)
        assert gone.status_code == 404
    finally:
        db.close()


def test_wb_stage_c_expenses_margin_push(client, wb_mock):
    headers = _admin(client)
    item_a = client.post(f"{API}/accounting/items", json={
        "sku": f"WBC2-{RUN}", "name": f"WB C {RUN}", "kind": "physical",
        "unit_code": "шт"}, headers=headers).json()
    ORDERS.clear()
    ORDERS.append({"uid": "M1", "isCancel": False, "dateClosed": "yes",
                   "date": "2026-09-26T12:00:00Z", "price": "1000.00",
                   "nmId": 301, "article": f"WBC2-{RUN}", "quantity": 2})

    from src.core.models import Company
    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from src.modules.mgmt_accounting.features.inventory import models as inv_m
    from sqlalchemy import select

    db = SessionLocal()
    org = db.scalar(select(Company).where(Company.name == "Основная"))
    loc = db.scalar(select(inv_m.Location).where(
        inv_m.Location.company_id == org.id,
        inv_m.Location.is_active.is_(True),
        inv_m.Location.kind != "transit").limit(1))

    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"wbc2-{RUN}", "connector_code": "wb_seller",
        "credentials": {"api_token": "t"},
        "config": {"base_url": wb_mock, "auto_create_orders": "on",
                   "warehouses": json.dumps({"wh-x": str(loc.id)})},
    }, headers=headers).json()
    cid = conn["id"]
    client.post(f"{API}/integrations/item-mappings", json={
        "connection_id": cid, "external_item_id": f"WBC2-{RUN}",
        "sku": f"WBC2-{RUN}", "item_id": item_a["id"]}, headers=headers)

    # склад 10 шт × себестоимость 100 (для маржи и push)
    cp = client.post(f"{API}/accounting/counterparties", json={
        "name": f"wbc-поставщик-{RUN}"}, headers=headers).json()
    po = client.post(f"{API}/accounting/purchase-orders", json={
        "counterparty_id": cp["id"], "currency": "RUB",
        "lines": [{"item_id": item_a["id"], "qty": "10",
                   "unit_price": "100"}]}, headers=headers).json()
    client.post(f"{API}/accounting/purchase-orders/{po['id']}/confirm",
                headers=headers)
    rc = client.post(f"{API}/accounting/receipts", json={
        "purchase_order_id": po["id"], "lines": [
            {"item_id": item_a["id"], "qty": "10", "unit_cost": "100",
             "location_id": str(loc.id)}]}, headers=headers)
    assert rc.status_code == 201, rc.text
    client.post(f"{API}/accounting/receipts/{rc.json()['id']}/post",
                headers=headers)

    sync = client.post(f"{API}/integrations/wb/sync", json={
        "connection_id": cid, "kinds": ["orders", "transactions"]},
        headers=headers)
    assert sync.status_code == 200, sync.text

    try:
        # §7.6: расходы по статьям «WB: …»; §7.7: payment без расхода
        txns = db.scalars(select(im.WBTransaction).where(
            im.WBTransaction.connection_id == cid)).all()
        by_op = {t.operation_id: t for t in txns}
        assert len(txns) == 7
        for suffix in ("c", "l", "s", "p", "t", "x"):
            assert by_op[f"{RUN}-{suffix}"].transaction_id is not None
        assert by_op[f"{RUN}-pay"].transaction_id is None  # выплата

        # повторный sync — без дублей
        client.post(f"{API}/integrations/wb/sync", json={
            "connection_id": cid, "kinds": ["transactions"]}, headers=headers)
        assert len(db.scalars(select(im.WBTransaction).where(
            im.WBTransaction.connection_id == cid)).all()) == 7

        cats = client.get(f"{API}/accounting/categories?limit=200",
                          headers=headers).json()
        cats = cats["items"] if isinstance(cats, dict) else cats
        names = {c["name"] for c in cats}
        assert {"Комиссия WB", "Логистика WB", "Хранение WB",
                "Штрафы WB", "Налог WB", "WB: Прочее"} <= names

        # §7.12: маржа — 1000 − (180+90.25+15+50+30+10) − cost(2×100)
        margin = client.get(f"{API}/integrations/wb/margin", params={
            "connection_id": cid, "date_from": "2026-09-01",
            "date_to": "2026-09-30"}, headers=headers).json()
        assert D(margin["revenue"]) == D("1000.00")
        assert D(margin["fees"]) == D("180.00")
        assert D(margin["logistics"]) == D("90.25")
        assert D(margin["storage"]) == D("15.00")
        assert D(margin["penalties"]) == D("50.00")
        assert D(margin["tax"]) == D("30.00")
        assert D(margin["other"]) == D("10.00")  # fallback §10.5
        assert D(margin["fees_total"]) == D("375.25")
        assert D(margin["profit"]) == D("624.75")  # без себестоимости

        m2 = client.get(f"{API}/integrations/wb/margin", params={
            "connection_id": cid, "date_from": "2026-09-01",
            "date_to": "2026-09-30", "include_cost": "true"},
            headers=headers).json()
        assert D(m2["cost"]) == D("200.00")
        assert D(m2["profit"]) == D("424.75")

        # §7.8: push по warehouse_id с привязанной локацией → qty=10
        push = client.post(f"{API}/integrations/wb/push-stocks", json={
            "connection_id": cid, "warehouse_id": "wh-x"}, headers=headers)
        assert push.status_code == 200, push.text
        assert push.json()["pushed"] == 1
        assert PUSHED, "в мок не ушёл PUT /api/v3/stocks/{warehouseId}"
        wh, body = PUSHED[-1]
        assert wh == "wh-x"
        assert body["stocks"][0]["vendorCode"] == f"WBC2-{RUN}"
        assert body["stocks"][0]["stock"] == 10
    finally:
        db.close()
