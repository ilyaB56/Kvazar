"""Этап C автоматизации продаж (sales-automation §10):

(C1) happy path end-to-end: вебхук → код выдан старейший (FIFO) → sold →
     письмо ушло через mock SMTP; повторный deliver → 409;
(C2) коды кончились → manual (insufficient_stock); пополнение + retry →
     выдан;
(C3) SMTP недоступен → учёт done (payment processed), notify failed
     (delivery_failed), retry после поднятия SMTP — доставлено;
(C4) коды не встречаются ни в одном событии/журнале (grep по outbox и
     audit — только отпечатки).

Инфраструктура: mock ЮKassa (9999), mock SMTP (9997), api-token,
smtp-connection, рецепт с delivery_channel=email.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
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
SMTP_MAILBOX: list[dict] = []
SMTP_DOWN = threading.Event()


class _YooKassaMock(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization", "") != \
                "Basic " + base64.b64encode(b"shop1:secret1").decode():
            self.send_response(401)
            self.end_headers()
            return
        path = self.path.split("?")[0]
        payment_id = path.rsplit("/", 1)[1] if path.startswith("/v3/payments/") else ""
        payment = PAYMENTS.get(payment_id)
        if payment is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payment).encode())

    def log_message(self, *args):
        pass


class _SmtpMock(BaseHTTPRequestHandler):
    """HTTP-заглушка SMTP не подходит — smtplib говорит по SMTP-протоколу.
    Поэтому ловим письмо на уровне локального TCP: принимаем DATA и
    складываем в mailbox; SMTP_DOWN → сразу закрываем соединение."""

    def handle(self):
        if SMTP_DOWN.is_set():
            self.request.close()
            return
        self.request.sendall(b"220 mock-smtp ready\r\n")
        data = b""
        got_data = False
        buf = b""
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                if not got_data:
                    if line.upper().startswith(b"DATA"):
                        self.request.sendall(b"354 go ahead\r\n")
                        got_data = True
                    elif line.upper().startswith(b"QUIT"):
                        self.request.sendall(b"221 bye\r\n")
                        self.request.close()
                        return
                    else:
                        self.request.sendall(b"250 ok\r\n")
                else:
                    if line == b".":
                        SMTP_MAILBOX.append({
                            "raw": data.decode("utf-8", errors="replace"),
                            "at": datetime.now(timezone.utc).isoformat(),
                        })
                        data = b""
                        got_data = False
                        self.request.sendall(b"250 accepted\r\n")
                    else:
                        data += line + b"\n"

    # это не HTTP-обработчик; заглушка типов для BaseHTTPRequestHandler
    def do_ALL(self):  # noqa: N802
        pass


@pytest.fixture(scope="module")
def yookassa_mock():
    server = HTTPServer(("127.0.0.1", 9989), _YooKassaMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture(scope="module")
def smtp_mock():
    import socketserver

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
        request_queue_size = 8

    server = Server(("127.0.0.1", 9997), None)
    server.RequestHandlerClass = type("H", (_SmtpMock,), {})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _payment_body(payment_id: str, lines: list[dict], amount: str,
                  email: str) -> dict:
    return {
        "id": payment_id, "status": "succeeded",
        "amount": {"value": amount, "currency": "RUB"},
        "metadata": {"email": email, "lines": lines},
    }


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=90)
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
def stage(client, admin_headers, yookassa_mock, smtp_mock):
    item = client.post(f"{ACC}/items", json={
        "sku": f"C-DIGI-{RUN}", "name": f"c код {RUN}", "kind": "digital",
        "unit_code": "лицензия", "tracking": "serial", "sale_price": "500.00",
    }, headers=admin_headers).json()
    loc = next(row for row in client.get(f"{ACC}/locations", headers=admin_headers).json()
               if row["name"] == "Цифровой склад")
    supplier = client.post(f"{ACC}/counterparties", json={
        "name": f"c-поставщик-{RUN}"}, headers=admin_headers).json()
    account = client.post(f"{ACC}/accounts", json={
        "name": f"c-счёт-{RUN}", "currency": "RUB"}, headers=admin_headers).json()

    # старейшая партия: 3 кода B-старше (проверка FIFO в C1)
    older_date = (date.today() - timedelta(days=7)).isoformat()
    rec_old = client.post(f"{ACC}/receipts", json={
        "counterparty_id": supplier["id"],
        "lines": [{"item_id": item["id"], "qty": "3", "unit_cost": "100",
                   "location_id": loc["id"],
                   "serial_codes": [f"C-OLD-{RUN}-{i}" for i in range(3)]}],
        "moved_at": older_date,
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{rec_old['id']}/post", headers=admin_headers)
    # свежая партия: 2 кода
    rec_new = client.post(f"{ACC}/receipts", json={
        "counterparty_id": supplier["id"],
        "lines": [{"item_id": item["id"], "qty": "2", "unit_cost": "120",
                   "location_id": loc["id"],
                   "serial_codes": [f"C-NEW-{RUN}-{i}" for i in range(2)]}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{rec_new['id']}/post", headers=admin_headers)

    api_token = client.post(f"{API}/admin/api-tokens", json={
        "name": f"c-flow-{RUN}", "role": "user"}, headers=admin_headers).json()["token"]
    api_conn = client.post(f"{API}/integrations/connections", json={
        "name": f"c-api-{RUN}", "connector_code": "http_rest",
        "credentials": {"api_key": api_token},
        "config": {"base_url": "http://127.0.0.1:8000/api/v1", "auth_style": "none"},
    }, headers=admin_headers).json()

    # credentials пусты: mock-SMTP не объявляет AUTH, логин не нужен
    smtp_conn = client.post(f"{API}/integrations/connections", json={
        "name": f"c-smtp-{RUN}", "connector_code": "smtp",
        "credentials": {},
        "config": {"host": "127.0.0.1", "port": 9997, "use_tls": False,
                   "from_email": "sales@test.local", "timeout_seconds": 5},
    }, headers=admin_headers).json()

    yk_conn = client.post(f"{API}/integrations/connections", json={
        "name": f"c-yk-{RUN}", "connector_code": "yookassa",
        "credentials": {"shop_id": "shop1", "secret_key": "secret1"},
        "config": {"base_url": "http://127.0.0.1:9989/v3"},
    }, headers=admin_headers).json()
    hook = client.post(f"{API}/integrations/webhooks", json={
        "name": f"c-hook-{RUN}", "target_module": "payments",
        "connection_id": yk_conn["id"],
    }, headers=admin_headers).json()

    client.post(f"{API}/integrations/item-mappings", json={
        "connection_id": yk_conn["id"], "external_item_id": f"site-{RUN}",
        "sku": item["sku"], "item_id": item["id"],
    }, headers=admin_headers)

    recipe = client.post(f"{API}/integrations/recipes", json={
        "name": f"c-flow-recipe-{RUN}",
        "definition": {
            "trigger_event": "integration.payment.received",
            "action": {
                "type": "sales_flow",
                "connection_id": yk_conn["id"],
                "config": {
                    "account_id": account["id"],
                    "price_tolerance": "0",
                    "on_no_items": "transaction_only",
                    "delivery_channel": "email",
                    "smtp_connection_id": smtp_conn["id"],
                },
            },
            "api_connection_id": api_conn["id"],
        },
    }, headers=admin_headers).json()
    client.post(f"{API}/integrations/recipes/{recipe['id']}/publish", headers=admin_headers)

    return {"item": item, "loc": loc, "supplier": supplier, "account": account,
            "api_conn": api_conn, "smtp": smtp_conn, "yk": yk_conn,
            "hook": hook, "recipe": recipe}


def _fire(client, stage, payment_id: str):
    return client.post(f"{API}/integrations/hooks/{stage['hook']['id']}",
                      json={"type": "notification", "event": "payment.succeeded",
                            "object": PAYMENTS[payment_id]})


def _mailbox_since(marker: str) -> list[dict]:
    # тело письма с кириллицей уходит base64 — маркер ищем в To-заголовке
    # (ASCII) или декодированном теле
    import base64 as _b64
    def _decoded(raw: str) -> str:
        out = raw
        for chunk in raw.splitlines():
            if len(chunk) > 20 and "=" not in chunk[:4]:
                try:
                    out += _b64.b64decode(chunk, validate=True).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
        return out
    return [m for m in SMTP_MAILBOX
            if marker in m["raw"] or marker in _decoded(m["raw"])]


def test_c1_happy_path_fifo_sold_email(client, admin_headers, stage):
    """(C1) вебхук → заказ → отгрузка FIFO (старейшие) → deliver → письмо;
    повторный deliver → 409."""
    payment_id = f"c1-{RUN}"
    email = f"c1{RUN}@example.com"
    PAYMENTS[payment_id] = _payment_body(
        payment_id,
        [{"external_id": f"site-{RUN}", "sku": stage["item"]["sku"],
          "qty": 2, "price": "500.00"}],
        "1000.00", email)

    response = _fire(client, stage, payment_id)
    assert response.status_code == 202, response.text
    flow = response.json().get("flow") or {}
    assert flow.get("status") == "done", flow

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    assert payment["status"] == "processed"
    assert payment["shipment_id"], "отгрузка не создана"

    shipment = client.get(f"{ACC}/shipments/{payment['shipment_id']}",
                          headers=admin_headers).json()
    assert shipment["status"] == "posted"
    # FIFO: выданы коды старейшей партии (C-OLD), не свежие (C-NEW);
    # внутри партии порядок не детерминирован (UUID-ключ) — важна партия
    codes = shipment["lines"][0]["serial_codes"]
    assert len(codes) == 2 and all(c.startswith(f"C-OLD-{RUN}-") for c in codes), codes

    # коды sold в БД (проверка через API: баланс цифрового склада уменьшился)
    balances = client.get(f"{ACC}/stock/balances", params={
        "item_id": stage["item"]["id"], "location_id": stage["loc"]["id"]},
        headers=admin_headers).json()
    assert balances and Decimal(balances[0]["qty"]) == Decimal("3")  # 5 − 2

    # письмо ушло с кодами
    letters = _mailbox_since(payment_id)
    assert letters, "письмо не дошло в mock SMTP"
    assert f"C-OLD-{RUN}-0" in letters[-1]["raw"]
    assert email in letters[-1]["raw"]

    # повторный deliver → 409 с фактом
    again = client.post(f"{ACC}/shipments/{payment['shipment_id']}/deliver",
                        json={"channel_note": "email"}, headers=admin_headers)
    assert again.status_code == 409
    assert "already delivered" in again.text


def test_c2_out_of_stock_then_retry(client, admin_headers, stage):
    """(C2) кодов не хватает → manual insufficient_stock; пополнение +
    retry → выдан."""
    payment_id = f"c2-{RUN}"
    email = f"c2{RUN}@example.com"
    PAYMENTS[payment_id] = _payment_body(
        payment_id,
        [{"external_id": f"site-{RUN}", "sku": stage["item"]["sku"],
          "qty": 10, "price": "500.00"}],  # на складе 3
        "5000.00", email)
    response = _fire(client, stage, payment_id)
    assert response.status_code == 202
    flow = response.json().get("flow") or {}
    assert flow.get("status") == "manual", flow
    assert "insufficient_stock" in flow.get("error", "")

    payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
    payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
    assert payment["status"] == "manual"
    assert payment["error_reason"] == "insufficient_stock"
    assert payment["transaction_id"], "деньги учтены (§8)"

    # пополнение: 10 кодов
    rec = client.post(f"{ACC}/receipts", json={
        "counterparty_id": stage["supplier"]["id"],
        "lines": [{"item_id": stage["item"]["id"], "qty": "10", "unit_cost": "110",
                   "location_id": stage["loc"]["id"],
                   "serial_codes": [f"C-R2-{RUN}-{i}" for i in range(10)]}],
    }, headers=admin_headers).json()
    client.post(f"{ACC}/receipts/{rec['id']}/post", headers=admin_headers)

    retry = client.post(f"{API}/integrations/payments/{payment['id']}/retry",
                        headers=admin_headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["flow_status"] == "done", retry.text

    payment = client.get(f"{API}/integrations/payments/{payment['id']}",
                         headers=admin_headers).json()
    assert payment["status"] == "processed"
    assert payment["shipment_id"]
    assert _mailbox_since(payment_id), "письмо после retry не дошло"


def test_c3_smtp_down_notify_failed_then_retry(client, admin_headers, stage):
    """(C3) SMTP недоступен → учёт done (payment processed), флоу manual
    delivery_failed; подняли SMTP → retry → доставлено."""
    SMTP_DOWN.set()
    try:
        payment_id = f"c3-{RUN}"
        email = f"c3{RUN}@example.com"
        PAYMENTS[payment_id] = _payment_body(
            payment_id,
            [{"external_id": f"site-{RUN}", "sku": stage["item"]["sku"],
              "qty": 1, "price": "500.00"}],
            "500.00", email)
        response = _fire(client, stage, payment_id)
        assert response.status_code == 202
        flow = response.json().get("flow") or {}
        assert flow.get("status") == "manual", flow
        assert "delivery_failed" in flow.get("error", "")

        payments = client.get(f"{API}/integrations/payments", headers=admin_headers).json()
        payment = next(p for p in payments if p["provider_payment_id"] == payment_id)
        # §8: учёт done — платёж processed, manual только у флоу
        assert payment["status"] == "processed"
        assert payment["error_reason"] == "delivery_failed"
        assert payment["shipment_id"] and payment["transaction_id"]
        # deliver уже прошёл (разовая выдача) — повтор не нужен
    finally:
        SMTP_DOWN.clear()

    retry = client.post(f"{API}/integrations/payments/{payment['id']}/retry",
                        headers=admin_headers)
    assert retry.status_code == 200
    assert retry.json()["flow_status"] == "done", retry.text
    assert _mailbox_since(payment_id), "письмо после retry не дошло"


def test_c4_no_codes_in_events_or_logs(client, admin_headers, stage):
    """(C4) коды-активы не встречаются в событиях шины и журнале аудита
    (только отпечатки/факты)."""
    outbox = client.get(f"{API}/events/outbox?limit=500", headers=admin_headers).json()
    leaked = [
        e for e in outbox
        if any(f"C-OLD-{RUN}-" in str(e.get("payload", {}))
               or f"C-NEW-{RUN}-" in str(e.get("payload", {}))
               or f"C-R2-{RUN}-" in str(e.get("payload", {}))
               for _ in [0])
    ]
    assert not leaked, f"codes leaked into outbox: {[e['event_name'] for e in leaked][:5]}"

    log = client.get(f"{API}/events/log?limit=500", headers=admin_headers).json()
    leaked_log = [
        e for e in log
        if f"C-OLD-{RUN}-" in str(e.get("payload", {}))
        or f"C-NEW-{RUN}-" in str(e.get("payload", {}))
        or f"C-R2-{RUN}-" in str(e.get("payload", {}))
    ]
    assert not leaked_log, f"codes leaked into audit log: {leaked_log[:2]}"

    # событие delivered есть и несёт только факт
    delivered = [e for e in outbox if e["event_name"] == "acc.shipment.delivered"]
    assert delivered, "acc.shipment.delivered не опубликовано"
    assert all("serial" not in str(e.get("payload", {})).lower()[:200]
               for e in delivered)
