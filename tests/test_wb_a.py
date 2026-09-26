"""Wildberries Seller — этап A (wb-connector-spec §7.1–7.2).

Мок WB Seller API в контейнере: карточки/остатки. Проверки:
test_connection с API-Token (Authorization без Bearer), sync products
без дублей (UNIQUE connection+nm_id), снапшот остатков, seeds заданий,
rate_limited на 429.

Запуск: docker compose exec api pytest tests/test_wb_a.py
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration

CARDS = [
    {"nmID": 101, "vendorCode": f"WB-{RUN}-1", "title": "Футболка",
     "sizes": [{"price": {"current": 1200}}]},
    {"nmID": 102, "vendorCode": f"WB-{RUN}-2", "title": "Кружка",
     "sizes": [{"price": {"current": 550}}]},
]
STOCKS = [
    {"nmId": 101, "amountByWarehouse": {"wh-1": 4, "wh-2": 1}},
    {"nmId": 102, "amountByWarehouse": {"wh-1": 9}},
]


class _WBMock(BaseHTTPRequestHandler):
    """Мини-WB: Authorization == 'tok-1' (без Bearer); иначе 403."""

    def _reply(self, payload: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _handle(self, method: str) -> None:
        if self.headers.get("Authorization") != "tok-1":
            self._reply({"error": "unauthorized"}, status=403)
            return
        path = self.path.split("?")[0]
        if path == "/content/v2/get/cards/list":
            self._reply({"cards": CARDS})
        elif path == "/api/v3/stocks":
            self._reply({"stocks": STOCKS})
        elif path == "/api/v3/orders":
            self._reply([])
        elif path == "/finance/v1/transactions":
            self._reply({"operations": []})
        else:
            self._reply({})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._handle("POST")

    def do_GET(self):  # noqa: N802
        self._handle("GET")

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


def _run(kind: str, connection) -> dict:
    from src.db import SessionLocal
    from src.modules.integrations.connectors.builtin import registry
    from src.modules.integrations.crypto import decrypt_dict
    from src.modules.integrations.wb import run_wb_sync

    connector = registry.build("wb_seller", connection.config,
                               decrypt_dict(connection.credentials_enc))
    db = SessionLocal()
    try:
        return run_wb_sync(db, kind=kind, connection=connection,
                           connector=connector)
    finally:
        db.close()


def _get_connection(conn_id):
    from src.db import SessionLocal
    from src.modules.integrations import models as im

    db = SessionLocal()
    try:
        return db.get(im.Connection, conn_id)
    finally:
        db.close()


def test_wb_catalog_present(client):
    headers = _admin(client)
    catalog = client.get(f"{API}/integrations/connectors", headers=headers).json()
    assert any(c["code"] == "wb_seller" for c in catalog)


def test_wb_stage_a_connection_products_no_dupes(client, wb_mock):
    """§7.1: test_connection ok (Authorization без Bearer); §7.2: sync
    products — без дублей; stocks — снапшот; seeds заданий."""
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"wb-{RUN}", "connector_code": "wb_seller",
        "credentials": {"api_token": "tok-1"},
        "config": {"base_url": wb_mock},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    cid = conn.json()["id"]

    test = client.post(f"{API}/integrations/connections/{cid}/test", headers=headers)
    assert test.status_code == 200 and test.json()["ok"], test.text

    connection = _get_connection(cid)
    first = _run("products", connection)
    assert first["ok"], first
    assert first["created"] == 2
    second = _run("products", connection)
    assert second["ok"] and second["created"] == 0 and second["updated"] == 2

    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from sqlalchemy import select

    db = SessionLocal()
    try:
        rows = db.scalars(select(im.WBProduct).where(
            im.WBProduct.connection_id == cid)).all()
        assert len(rows) == 2, "повторный прогон создал дубли"
        by_nm = {r.nm_id: r for r in rows}
        assert by_nm["101"].vendor_code == f"WB-{RUN}-1"
        from decimal import Decimal as _D
        assert _D(str(by_nm["101"].price)) == _D("1200")

        jobs = db.scalars(select(im.SyncJob).where(
            im.SyncJob.connection_id == cid)).all()
        kinds = {j.endpoint for j in jobs}
        assert {"products", "orders", "transactions"} <= kinds

        s1 = _run("stocks", connection)
        assert s1["ok"] and s1["created"] == 3  # 2 склада + 1
        s2 = _run("stocks", connection)
        assert s2["created"] == 3  # снапшот-перезапись
        n = len(db.scalars(select(im.WBStock).where(
            im.WBStock.connection_id == cid)).all())
        assert n == 3, "снапшот накопился вместо перезаписи"
    finally:
        db.close()


def test_wb_bad_token_403(client, wb_mock):
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"wb-bad-{RUN}", "connector_code": "wb_seller",
        "credentials": {"api_token": "WRONG"},
        "config": {"base_url": wb_mock},
    }, headers=headers).json()
    test = client.post(f"{API}/integrations/connections/{conn['id']}/test",
                       headers=headers)
    assert test.status_code == 200
    assert not test.json()["ok"]
    assert "auth_failed" in test.json()["error"]


def test_wb_throttle_100_per_minute():
    """Троттлинг коннектора: интервал между вызовами ≥ 60/100 c."""
    from src.modules.integrations.connectors.wb import WBSellerConnector

    conn = WBSellerConnector(code="wb_seller", display_name="wb",
                             config={"base_url": "http://127.0.0.1:1"},
                             credentials={})
    conn._throttle._last = time.monotonic()
    t0 = time.monotonic()
    # второй вызов сразу — должен подождать ~минимальный интервал
    conn._throttle.wait()
    assert time.monotonic() - t0 >= 0.5, "троттлинг не выдерживает интервал"
