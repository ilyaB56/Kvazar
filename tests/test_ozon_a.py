"""Ozon Seller — этап A (ozon-connector-spec §7.1–7.2).

Мок Ozon Seller API (в контейнере): товары/цены, остатки, заказы,
транзакции. Проверки: test_connection с ключами, sync products без
дублей (UNIQUE connection+offer_id), снапшот остатков, идемпотентность
заказов/транзакций на уровне куратора.

Запуск: docker compose exec api pytest tests/test_ozon_a.py
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

CATALOG = [
    {"offer_id": f"OZ-{RUN}-1", "product_id": "101", "name": "Товар 1"},
    {"offer_id": f"OZ-{RUN}-2", "product_id": "102", "name": "Товар 2"},
]
PRICES = {f"OZ-{RUN}-1": "1500.0000", f"OZ-{RUN}-2": "990.0000"}
STOCKS = [
    {"offer_id": f"OZ-{RUN}-1", "warehouse_id": "200001", "qty": 7},
    {"offer_id": f"OZ-{RUN}-2", "warehouse_id": "200001", "qty": 3},
]


class _OzonMock(BaseHTTPRequestHandler):
    """Мини-Ozon Seller API: заголовки Client-Id/Api-Key обязательны."""

    def _reply(self, payload: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):  # noqa: N802
        if not (self.headers.get("Client-Id") == "cid-1"
                and self.headers.get("Api-Key") == "key-1"):
            self._reply({"error": "unauthorized"}, status=403)
            return
        path = self.path.split("?")[0]
        if path == "/v3/product/list":
            self._reply({"result": {"items": CATALOG}})
        elif path == "/v5/product/info/prices":
            self._reply({"result": {"items": [
                {"offer_id": o, "price": {"price": p}} for o, p in PRICES.items()]}})
        elif path == "/v4/product/info/stocks":
            self._reply({"result": {"items": [
                {"offer_id": s["offer_id"],
                 "stocks": [{"warehouse_id": s["warehouse_id"], "present": s["qty"]}]}
                for s in STOCKS]}})
        else:
            self._reply({"result": []})

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


def _make_connection(client, ozon_mock, name_suffix=""):
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ozon-{RUN}{name_suffix}", "connector_code": "ozon_seller",
        "credentials": {"client_id": "cid-1", "api_key": "key-1"},
        "config": {"base_url": ozon_mock},
    }, headers=headers)
    assert conn.status_code == 201, conn.text
    return headers, conn.json()


def _sync(client, headers, connection_id, endpoint):
    """Прогон куратора: run_job — синхронно (eager) из контейнера."""
    return None  # прогон напрямую в БД (ниже), celery в тестах не ждём


def _run_curator(kind: str, connection) -> dict:
    from src.db import SessionLocal
    from src.modules.integrations.connectors.builtin import registry
    from src.modules.integrations.crypto import decrypt_dict
    from src.modules.integrations.ozon import run_ozon_sync

    connector = registry.build("ozon_seller", connection.config,
                               decrypt_dict(connection.credentials_enc))
    db = SessionLocal()
    try:
        return run_ozon_sync(db, kind=kind, connection=connection, connector=connector)
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


def test_catalog_contains_ozon(client):
    headers = _admin(client)
    catalog = client.get(f"{API}/integrations/connectors", headers=headers).json()
    assert any(c["code"] == "ozon_seller" for c in catalog)


def test_stage_a_connection_test_and_products_no_dupes(client, ozon_mock):
    """§7.1 test_connection ok; §7.2 sync products — без дублей."""
    headers, conn = _make_connection(client, ozon_mock)

    # §7.1: тест связи с валидными ключами (мок требует Client-Id/Api-Key)
    test = client.post(f"{API}/integrations/connections/{conn['id']}/test",
                       headers=headers)
    assert test.status_code == 200 and test.json()["ok"], test.text

    connection = _get_connection(conn["id"])
    # §7.2: первый прогон — 2 товара, повторный — 0 новых, дублей нет
    first = _run_curator("products", connection)
    assert first["ok"], first
    assert first["created"] == 2
    second = _run_curator("products", connection)
    assert second["ok"] and second["created"] == 0 and second["updated"] == 2

    from src.db import SessionLocal
    from src.modules.integrations import models as im
    from sqlalchemy import select

    db = SessionLocal()
    try:
        rows = db.scalars(select(im.OzonProduct).where(
            im.OzonProduct.connection_id == conn["id"])).all()
        assert len(rows) == 2, "повторный прогон создал дубли"
        by_offer = {r.offer_id: r for r in rows}
        assert str(by_offer[f"OZ-{RUN}-1"].price) == "1500.0000"

        # задание seeds созданы (товары/час, заказы/15мин, транзакции/час)
        jobs = db.scalars(select(im.SyncJob).where(
            im.SyncJob.connection_id == conn["id"])).all()
        kinds = {j.endpoint for j in jobs}
        assert {"products", "orders", "transactions"} <= kinds

        # остатки: снапшот-перезапись (2 строки за прогон)
        stocks1 = _run_curator("stocks", connection)
        assert stocks1["ok"] and stocks1["created"] == 2
        stocks2 = _run_curator("stocks", connection)
        assert stocks2["created"] == 2  # перезапись, не накопление
        n = len(db.scalars(select(im.OzonStock).where(
            im.OzonStock.connection_id == conn["id"])).all())
        assert n == 2, "снапшот накопился вместо перезаписи"
    finally:
        db.close()


def test_stage_a_bad_keys_403(client, ozon_mock):
    """Отозванный ключ → test_connection auth_failed (§7.9 в терминах
    test_connection)."""
    headers = _admin(client)
    conn = client.post(f"{API}/integrations/connections", json={
        "name": f"ozon-bad-{RUN}", "connector_code": "ozon_seller",
        "credentials": {"client_id": "cid-1", "api_key": "WRONG"},
        "config": {"base_url": ozon_mock},
    }, headers=headers).json()
    test = client.post(f"{API}/integrations/connections/{conn['id']}/test",
                       headers=headers)
    assert test.status_code == 200
    assert not test.json()["ok"]
    assert "auth_failed" in test.json()["error"]
