"""Unit-тесты: события, шифрование, Connector SDK (без БД)."""

from __future__ import annotations

import hmac
import hashlib

import pytest

from src.core import events
from src.modules.integrations.crypto import decrypt_dict, encrypt_dict
from src.modules.integrations.sdk import ConnectorRegistry, BaseConnector, Capabilities, ConnectorResult


def test_event_bus_subscribe_and_dispatch():
    received = []
    events.subscribe("test.event", lambda payload: received.append(payload))
    events._handlers["test.event"].append(lambda p: received.append(p))

    class FakeOutbox:
        event_name = "test.event"
        payload = {"x": 1}
        processed = False
        processed_at = None

    # dispatch работает с любыми объектами нужной формы
    class FakeDB:
        def execute(self, *a, **k):
            class R:
                def scalars(self):
                    class S:
                        def all(self):
                            return [FakeOutbox()]

                    return S()

            return R()

        def commit(self):
            pass

    events.dispatch_outbox(FakeDB())  # type: ignore[arg-type]
    assert {"x": 1} in received


def test_crypto_roundtrip():
    secrets = {"api_key": "secret-123", "token": "abc"}
    blob = encrypt_dict(secrets)
    assert "secret-123" not in blob
    assert decrypt_dict(blob) == secrets


def test_connector_registry():
    reg = ConnectorRegistry()

    class EchoConnector(BaseConnector):
        code = "echo_test"
        display_name = "Echo"
        capabilities = Capabilities(fetch=True)

        def test_connection(self):
            return ConnectorResult(ok=True)

        def fetch(self, endpoint="", params=None):
            return ConnectorResult(ok=True, data={"echo": endpoint})

    reg.register(EchoConnector)
    connector = reg.build("echo_test", config={}, credentials={})
    assert connector.fetch("/ping").data == {"echo": "/ping"}
    assert any(c["code"] == "echo_test" for c in reg.available())
    with pytest.raises(KeyError):
        reg.build("unknown", {}, {})


def test_webhook_hmac_verify():
    from src.modules.integrations.connectors.builtin import HttpRestConnector

    connector = HttpRestConnector(
        code="http_rest", display_name="x",
        credentials={"webhook_secret": "whsec"},
    )
    body = b'{"payment": 1}'
    signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert connector.verify_webhook({"x-signature": signature}, body)
    assert not connector.verify_webhook({"x-signature": "bad"}, body)


def test_yookassa_normalize_flat_metadata():
    """Плоский metadata {item_sku, qty} (простой сайт/демо) → одна строка
    платежа; массив metadata.lines — как раньше."""
    from src.modules.integrations.connectors.acquiring import YooKassaConnector

    conn = YooKassaConnector(
        code=YooKassaConnector.code, display_name=YooKassaConnector.display_name,
        config={"base_url": "http://127.0.0.1:1"}, credentials={})
    flat = conn.normalize({
        "object": {
            "id": "flat-1", "status": "succeeded",
            "amount": {"value": "990.00", "currency": "RUB"},
            "metadata": {"item_sku": "T-1", "qty": "2"},
            "recipient": {"email": "b@x.ru", "name": "Б"},
        }})
    assert flat["lines"] == [{
        "external_id": "T-1", "sku": "T-1", "name": "",
        "qty": "2", "price": "990.00"}]
    assert flat["buyer"]["email"] == "b@x.ru"

    listed = conn.normalize({
        "object": {
            "id": "list-1", "amount": {"value": "10.00"},
            "metadata": {"lines": [{"external_id": "s-1", "sku": "S",
                                    "qty": 1, "price": "10.00"}]}}})
    assert listed["lines"][0]["external_id"] == "s-1"
    assert listed["lines"][0]["price"] == "10.00"
