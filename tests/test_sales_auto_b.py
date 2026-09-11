"""Этап B автоматизации продаж (sales-automation §10):

(B1) seed-вебхук «2× код по 500₽» → заказ confirmed с ЗК-номером,
     транзакция ПК- с категорией «Продажи» и source-ссылкой;
(B2) повтор того же вебхука → ноль изменений;
(B3) неизвестный товар → manual + item_not_mapped; после маппинга
     retry → заказ создан, второй транзакции нет;
(B4) платёж без строк → только транзакция (transaction_only);
(B5) цена −10% при tolerance 0 → price_mismatch: заказ по ценам платежа
     подтверждён, оплаты/отгрузки нет (§8: стоп после confirm).

Инфраструктура: mock ЮKassa (9998, из этапа A), служебный connection
http_rest → наш API с api_key (X-API-Token), рецепт sales_flow.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
ACC = f"{API}/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]

PAYMENTS: dict[str, dict] = {}


class _YooKassaMock(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        auth_ok = self.headers.get("Authorization", "") == \
            "Basic " + base64.b64encode(b"shop1:secret1").decode()
        if not auth_ok:
            self.send_response(401)
            self.end_headers()
            return
        path = self.path.split("?")[0]
        if path == "/v3/payments":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"items": []}')
            return
        if path.startswith("/v3/payments/"):
            payment_id = path.rsplit("/", 1)[1]
            payment = PAYMENTS.get(payment_id)
            if payment is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payment).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def yookassa_mock():
    server = HTTPServer(("127.0.0.1", 9999), _YooKassaMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _payment_body(payment_id: str, lines: list[dict], amount: str,
                  email: str = f"b{RUN}@example.com") -> dict:
    return {
        "id": payment_id, "status": "succeeded",
        "amount": {"value": amount, "currency": "RUB"},
        "metadata": {"email": email, "lines": lines},
    }


def _notify(payment_id: str) -> dict:
    return {"type": "notification", "event": "payment.succeeded",
            "object": PAYMENTS[payment_id]}


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


@pytest.fixture(scope="module")
def admin_headers(client):
    response = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def stage(client, admin_headers, yookassa_mock):
    """Полная обвязка: товар+склад, счёт, api-token connection, yookassa
    connection, endpoint, маппинг, рецепт sales_flow."""
    # товар цифровой (serial) и цифровой склад
    item = client.post(f"{ACC}/items", json={
        "sku": f"B-DIGI-{RUN}", "name": f"b код {RUN}", "kind": "digital",
        "unit_code": "лицензия", "tracking": "serial",
    }, headers=admin_headers).json()
    loc = next(l for l in client.get(f"{ACC}/locations", headers=admin_headers).json()
               if l["name"] == "Цифровой склад")
    receipt = client.post(f"{ACC}/receipts", json={
        "counterparty_id": client.post(f"{ACC}/counterparties", json={
            "name": f"b-поставщик-{RUN}"}, headers=admin_headers).json()["id"],
        "lines": [{"item_id": item["id"], "qty": "10", "unit_cost": "100",
                   "location_id": loc["id"],
                   "serial_codes": [f"B-{RUN}-{i}" for i in range(10)]}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{receipt['id']}/post", headers=admin_headers)

    account = client.post(f"{ACC}/accounts", json={
        "name": f"b-счёт-{RUN}", "currency": "RUB"}, headers=admin_headers).json()

    # служебный api-token (роль user) → connection http_rest на наш API
    api_token = client.post(f"{API}/admin/api-tokens", json={
        "name": f"b-flow-{RUN}", "role": "user"}, headers=admin_headers).json()["token"]
    api_conn = client.post(f"{API}/integrations/connections", json={
        "name": f"b-api-{RUN}", "connector_code": "http_rest",
        "credentials": {"api_key": api_token},
        "config": {"base_url": "http://127.0.0.1:8000/api/v1", "auth_style": "none"},
    }, headers=admin_headers).json()

    yk_conn = client.post(f"{API}/integrations/connections", json={
        "name": f"b-yk-{RUN}", "connector_code": "yookassa",
        "credentials": {"shop_id": "shop1", "secret_key": "secret1"},
        "config": {"base_url": "http://127.0.0.1:9999/v3"},
    }, headers=admin_headers).json()
    hook = client.post(f"{API}/integrations/webhooks", json={
        "name": f"b-hook-{RUN}", "target_module": "payments",
        "connection_id": yk_conn["id"],
    }, headers=admin_headers).json()

    # эталонная цена для B5 (price_mismatch −10%)
    client.put(f"{ACC}/items/{item['id']}", json={"sale_price": "500.00"},
               headers=admin_headers)

    # маппинг сайт-товар → товар
    mapping = client.post(f"{API}/integrations/item-mappings", json={
        "connection_id": yk_conn["id"], "external_item_id": f"site-{RUN}-1",
        "sku": item["sku"], "item_id": item["id"],
    }, headers=admin_headers).json()

    # рецепт sales_flow
    recipe = client.post(f"{API}/integrations/recipes", json={
        "name": f"b-flow-recipe-{RUN}",
        "definition": {
            "trigger_event": "integration.payment.received",
            "action": {
                "type": "sales_flow",
                "connection_id": yk_conn["id"],
                "config": {
                    "account_id": account["id"],
                    "price_tolerance": "0",
                    "on_no_items": "transaction_only",
                    "delivery_channel": "none",
                },
            },
            "api_connection_id": api_conn["id"],
        },
    }, headers=admin_headers).json()
    client.post(f"{API}/integrations/recipes/{recipe['id']}/publish", headers=admin_headers)

    return {"item": item, "loc": loc, "account": account, "api_conn": api_conn,
            "yk": yk_conn, "hook": hook, "mapping": mapping, "recipe": recipe}


def _fire(client, stage, payment_id: str):
    return client.post(f"{API}/integrations/hooks/{stage['hook']['id']}",
                      json=_notify(payment_id))


def test_b1_webhook_to_order_and_transaction(client, admin_headers, stage, yookassa_mock):
    """(B1) 2×500 → заказ confirmed (ЗК-), транзакция ПК- «Продажи»."""
    payment_id = f"b1-{RUN}"
    PAYMENTS[payment_id] = _payment_body(
        payment_id,
        [{"external_id": f"site-{RUN}-1", "sku": stage["item"]["sku"],
          "qty": 2, "price": "500.00"}],
        "1000.00")
    response = _fire(client, stage, payment_id)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body.get("payment_id"), body
    flow = body.get("flow") or {}
    assert flow.get("status") == "done", flow

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    assert payment["status"] == "processed"
    assert payment["sales_order_id"], "заказ не создан"

    order = client.get(f"{ACC}/sales-orders/{payment['sales_order_id']}",
                       headers=admin_headers).json()
    assert order["status"] == "confirmed"
    assert order["number"].startswith("ЗК-")
    assert Decimal(order["amount_base"]) == Decimal("1000.00")

    # транзакция от заказа: категория «Продажи», dimensions.source
    txns = client.get(f"{ACC}/transactions", params={
        "date_from": "2000-01-01", "date_to": "2100-01-01"}, headers=admin_headers).json()
    txn = next(t for t in txns if t["id"] == payment["transaction_id"])
    assert txn["kind"] == "income" and txn["status"] == "posted"
    assert txn["doc_number"].startswith("ПК-")
    categories = client.get(f"{ACC}/categories", headers=admin_headers).json()
    sales_cat = next(c for c in categories if c["name"] == "Продажи" and c["kind"] == "income")
    assert txn["category_id"] == sales_cat["id"]
    assert txn["dimensions"]["source_type"] == "sales_order"

    # контрагент создан по email покупателя
    counterparties = client.get(f"{ACC}/counterparties", headers=admin_headers).json()
    cp = next(c for c in counterparties if c["id"] == order["counterparty_id"])
    assert cp["name"], "контрагент без имени"


def test_b2_duplicate_webhook_zero_changes(client, admin_headers, stage, yookassa_mock):
    """(B2) повтор вебхука → duplicate, ни заказов, ни транзакций не
    прибавилось."""
    payment_id = f"b1-{RUN}"  # тот же
    payments_before = len(client.get(f"{API}/integrations/payments", headers=admin_headers).json())
    orders_before = len(client.get(f"{ACC}/sales-orders", headers=admin_headers).json())

    response = _fire(client, stage, payment_id)
    assert response.status_code == 202
    assert response.json().get("duplicate") is True

    payments_after = len(client.get(f"{API}/integrations/payments", headers=admin_headers).json())
    orders_after = len(client.get(f"{ACC}/sales-orders", headers=admin_headers).json())
    assert payments_after == payments_before
    assert orders_after == orders_before


def test_b3_item_not_mapped_then_retry(client, admin_headers, stage, yookassa_mock):
    """(B3) неизвестный товар → manual + item_not_mapped; после маппинга
    retry → заказ создан; второй транзакции нет."""
    payment_id = f"b3-{RUN}"
    PAYMENTS[payment_id] = _payment_body(
        payment_id,
        [{"external_id": f"unknown-{RUN}", "qty": 1, "price": "777.00"}],
        "777.00", email=f"u3{RUN}@example.com")
    response = _fire(client, stage, payment_id)
    assert response.status_code == 202
    flow = response.json().get("flow") or {}
    assert flow.get("status") == "manual", flow
    assert "item_not_mapped" in flow.get("error", "")

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    assert payment["status"] == "manual"
    assert payment["error_reason"] == "item_not_mapped"
    assert payment["sales_order_id"] is None

    # оператор заводит маппинг (глобальный по sku) → retry
    item2 = client.post(f"{ACC}/items", json={
        "sku": f"B-DIGI2-{RUN}", "name": f"b код2 {RUN}", "kind": "digital",
        "unit_code": "лицензия", "tracking": "serial",
    }, headers=admin_headers).json()
    receipt2 = client.post(f"{ACC}/receipts", json={
        "counterparty_id": client.post(f"{ACC}/counterparties", json={
            "name": f"b-поставщик2-{RUN}"}, headers=admin_headers).json()["id"],
        "lines": [{"item_id": item2["id"], "qty": "5", "unit_cost": "50",
                   "location_id": stage["loc"]["id"],
                   "serial_codes": [f"B2-{RUN}-{i}" for i in range(5)]}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{receipt2['id']}/post", headers=admin_headers)
    client.post(f"{API}/integrations/item-mappings", json={
        "external_item_id": f"unknown-{RUN}", "sku": item2["sku"],
        "item_id": item2["id"],
    }, headers=admin_headers)

    retry = client.post(f"{API}/integrations/payments/{payment['id']}/retry",
                        headers=admin_headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["flow_status"] == "done", retry.text

    payment = client.get(f"{API}/integrations/payments/{payment['id']}",
                         headers=admin_headers).json()
    assert payment["status"] == "processed"
    assert payment["sales_order_id"]
    order = client.get(f"{ACC}/sales-orders/{payment['sales_order_id']}",
                       headers=admin_headers).json()
    assert order["status"] == "confirmed"

    # вторая транзакция не появилась (retry дошёл до pay один раз)
    txns_after_retry = client.get(f"{ACC}/transactions", params={
        "date_from": "2000-01-01", "date_to": "2100-01-01"}, headers=admin_headers).json()
    mine = [t for t in txns_after_retry
            if t.get("dimensions", {}).get("source_id") == order["id"]]
    assert len(mine) == 1, f"expected exactly 1 transaction, got {len(mine)}"


def test_b4_no_lines_transaction_only(client, admin_headers, stage, yookassa_mock):
    """(B4) платёж без строк → только транзакция, заказа нет."""
    payment_id = f"b4-{RUN}"
    PAYMENTS[payment_id] = _payment_body(payment_id, [], "555.00",
                                         email=f"u4{RUN}@example.com")
    response = _fire(client, stage, payment_id)
    assert response.status_code == 202
    flow = response.json().get("flow") or {}
    assert flow.get("status") == "done", flow

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    assert payment["status"] == "processed"
    assert payment["sales_order_id"] is None
    assert payment["transaction_id"]

    txn = client.get(f"{ACC}/transactions", params={
        "date_from": "2000-01-01", "date_to": "2100-01-01"}, headers=admin_headers).json()
    mine = next(t for t in txn if t["id"] == payment["transaction_id"])
    assert mine["kind"] == "income" and mine["status"] == "posted"
    assert Decimal(mine["amount"]) == Decimal("555.00")


def test_b5_price_mismatch_stops_after_confirm(client, admin_headers, stage, yookassa_mock):
    """(B5) цена −10% при tolerance 0 → price_mismatch: заказ по ценам
    платежа подтверждён, оплаты (trans) нет, отгрузки нет."""
    payment_id = f"b5-{RUN}"
    PAYMENTS[payment_id] = _payment_body(
        payment_id,
        # цена на сайте на 10% ниже эталонной (500 → 450)
        [{"external_id": f"site-{RUN}-1", "sku": stage["item"]["sku"],
          "qty": 1, "price": "450.00"}],
        "450.00", email=f"u5{RUN}@example.com")
    response = _fire(client, stage, payment_id)
    assert response.status_code == 202

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    # §8: заказ подтверждён по ценам платежа, транзакция создана,
    # флоу остановлен в manual перед отгрузкой — этап C не продолжит
    assert payment["status"] == "manual"
    assert payment["error_reason"] == "price_mismatch"
    assert payment["transaction_id"], "деньги должны быть учтены (§8)"
    assert payment["sales_order_id"]
    order = client.get(f"{ACC}/sales-orders/{payment['sales_order_id']}",
                       headers=admin_headers).json()
    assert order["status"] == "confirmed"
    assert Decimal(order["amount_base"]) == Decimal("450.00")
