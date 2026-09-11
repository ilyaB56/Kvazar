"""Сквозной e2e цикл ресурсов (resources-core-spec, этапы A–D):

закупка 10@100 → приёмка (средняя 100) → оплата «Закупки товаров» →
заказ клиента 6@250 → частичные отгрузки 4+2 → оплата «Продажи» →
маржа 1500/600/900 → сборка 2×материал → 1 изделие (себестоимость 200) →
цифровые коды: закупка 2 серийников → продажа с FIFO-выдачей (оба sold).

Поток — против живого API (стиль tests/test_roles.py: httpx + логин admin),
глубокие проверки серийников — хвостом через БД (стиль tests/test_inventory.py).
Деньги/количества — Decimal-строки (ADR-003), сравнения через Decimal.
Запуск: docker compose exec api pytest tests/test_resources_e2e.py
(нужны и API, и PostgreSQL; иначе pytest.skip, как в остальных DB/API-тестах).

Черновик подготовлен QA-агентом 2026-09-07 (статическая выверка по
роутерам/сервисам/smoke, без прогона) — после первого зелёного прогона
включить в регресс-набор; см. docs/design/pilots-readiness.md §1.5.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
ACC = "/api/v1/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]  # уникальные sku/имена — повторные прогоны не конфликтуют


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=10)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


@pytest.fixture(scope="module")
def admin_headers(client):
    response = client.post("/api/v1/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"),
    })
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def db():
    try:
        from src.db import SessionLocal, engine

        engine.connect().close()
    except Exception:
        pytest.skip("PostgreSQL not available")
    session = SessionLocal()
    yield session
    session.close()


# ---------- Хелперы ----------

def _post(client, headers, path: str, body: dict, expect: int = 201) -> dict:
    response = client.post(ACC + path, json=body, headers=headers)
    assert response.status_code == expect, \
        f"POST {path} -> {response.status_code}: {response.text[:200]}"
    return response.json()


def _get(client, headers, path: str):
    response = client.get(ACC + path, headers=headers)
    assert response.status_code == 200, f"GET {path} -> {response.status_code}"
    return response.json()


def _money(value) -> Decimal:
    """Суммы из API — строками; сравниваем через Decimal (ADR-003)."""
    return Decimal(str(value))


def _qty_on_hand(client, headers, item_id: str) -> Decimal:
    return sum(_money(b["qty"]) for b in
               _get(client, headers, f"/stock/balances?item_id={item_id}"))


# ---------- Сквозной цикл ----------

def test_resources_e2e_purchase_assemble_sell_digital(client, admin_headers, db):
    # --- Справочники: контрагенты, счёт, локации, номенклатура (sku уникальны)
    supplier = _post(client, admin_headers, "/counterparties",
                     {"name": f"e2e-поставщик-{RUN}"})
    customer = _post(client, admin_headers, "/counterparties",
                     {"name": f"e2e-клиент-{RUN}"})
    account = _post(client, admin_headers, "/accounts",
                    {"name": f"e2e-счёт-{RUN}", "currency": "RUB"})
    locations = {row["name"]: row for row in _get(client, admin_headers, "/locations")}
    main = locations["Основной склад"]

    material = _post(client, admin_headers, "/items", {
        "sku": f"E2E-MAT-{RUN}", "name": "e2e материал",
        "kind": "physical", "unit_code": "шт"})
    product = _post(client, admin_headers, "/items", {
        "sku": f"E2E-PRD-{RUN}", "name": "e2e изделие",
        "kind": "physical", "unit_code": "шт"})
    dig_item = _post(client, admin_headers, "/items", {
        "sku": f"E2E-DIG-{RUN}", "name": "e2e код пополнения",
        "kind": "digital", "unit_code": "лицензия"})
    assert dig_item["tracking"] == "serial"

    # --- 1. Закупка: заказ 10 @ 100 → приёмка → средняя 100
    po = _post(client, admin_headers, "/purchase-orders", {
        "counterparty_id": supplier["id"], "currency": "RUB",
        "lines": [{"item_id": material["id"], "qty": "10", "unit_price": "100"}]})
    assert po["status"] == "draft" and _money(po["amount_base"]) == Decimal("1000.00")
    po = _post(client, admin_headers, f"/purchase-orders/{po['id']}/confirm", {}, 200)
    assert po["status"] == "confirmed" and po["number"].startswith("ЗП-")

    receipt = _post(client, admin_headers, "/receipts", {
        "purchase_order_id": po["id"],
        "lines": [{"item_id": material["id"], "qty": "10"}]})  # цена — из заказа
    receipt = _post(client, admin_headers, f"/receipts/{receipt['id']}/post", {}, 200)
    assert receipt["status"] == "posted" and receipt["number"].startswith("ПМ-")
    assert _money(receipt["lines"][0]["unit_cost"]) == Decimal("100")

    mat_state = _get(client, admin_headers, f"/items/{material['id']}")
    assert _money(mat_state["avg_cost"]) == Decimal("100")  # средняя первого прихода
    balances = _get(client, admin_headers, f"/stock/balances?item_id={material['id']}")
    assert [(b["location_id"], _money(b["qty"]), _money(b["value"])) for b in balances] \
        == [(main["id"], Decimal("10"), Decimal("1000"))]

    # --- 2. Оплата закупки: СК-, категория «Закупки товаров», контрагент наследуется
    payment = _post(client, admin_headers, f"/purchase-orders/{po['id']}/pay",
                    {"account_id": account["id"], "amount": "1000"}, 200)
    assert payment["kind"] == "expense" and payment["status"] == "posted"
    assert payment["doc_number"].startswith("СК-")
    assert payment["counterparty_id"] == supplier["id"]
    assert payment["dimensions"] == {"source_type": "purchase_order", "source_id": po["id"]}
    purchase_cat = next(c for c in _get(client, admin_headers, "/categories")
                        if c["name"] == "Закупки товаров" and c["kind"] == "expense")
    assert payment["category_id"] == purchase_cat["id"]

    cp_bal = _get(client, admin_headers,
                  f"/reports/counterparty-balance?counterparty_id={supplier['id']}")
    assert _money(cp_bal["received_amount_base"]) == Decimal("1000.00")
    assert _money(cp_bal["paid_amount_base"]) == Decimal("1000.00")
    assert _money(cp_bal["balance"]) == Decimal("0.00")  # деньги сошлись

    # --- 3. Продажа: заказ клиента 6 @ 250 → частичные отгрузки 4 + 2
    so = _post(client, admin_headers, "/sales-orders", {
        "counterparty_id": customer["id"], "currency": "RUB",
        "lines": [{"item_id": material["id"], "qty": "6", "unit_price": "250"}]})
    assert _money(so["amount_base"]) == Decimal("1500.00")
    so = _post(client, admin_headers, f"/sales-orders/{so['id']}/confirm", {}, 200)
    assert so["status"] == "confirmed" and so["number"].startswith("ЗК-")
    assert _money(so["lines"][0]["reserved_qty"]) == Decimal("6")

    # негатив: отгрузка сверх заказа — 422 до всякого списания
    _post(client, admin_headers, "/shipments", {
        "sales_order_id": so["id"],
        "lines": [{"item_id": material["id"], "qty": "7"}]}, 422)

    sh1 = _post(client, admin_headers, "/shipments", {
        "sales_order_id": so["id"],
        "lines": [{"item_id": material["id"], "qty": "4"}]})
    sh1 = _post(client, admin_headers, f"/shipments/{sh1['id']}/post", {}, 200)
    assert sh1["status"] == "posted" and sh1["number"].startswith("ОТ-")
    assert _money(sh1["lines"][0]["amount_base"]) == Decimal("1000.00")  # 4 × 250

    so_mid = _get(client, admin_headers, f"/sales-orders/{so['id']}")
    assert so_mid["status"] == "partially_shipped"
    assert _money(so_mid["lines"][0]["reserved_qty"]) == Decimal("2")

    sh2 = _post(client, admin_headers, "/shipments", {
        "sales_order_id": so["id"],
        "lines": [{"item_id": material["id"], "qty": "2"}]})
    sh2 = _post(client, admin_headers, f"/shipments/{sh2['id']}/post", {}, 200)
    so_final = _get(client, admin_headers, f"/sales-orders/{so['id']}")
    assert so_final["status"] == "shipped"
    assert _money(so_final["lines"][0]["reserved_qty"]) == Decimal("0")

    # остаток 4 по средней 100 (расход среднюю не меняет), стоимость 400
    balances = _get(client, admin_headers, f"/stock/balances?item_id={material['id']}")
    assert len(balances) == 1 and _money(balances[0]["qty"]) == Decimal("4")
    assert _money(balances[0]["avg_cost"]) == Decimal("100")
    assert _money(balances[0]["value"]) == Decimal("400")

    # --- 4. Оплата продажи: ПК-, категория «Продажи»
    s_payment = _post(client, admin_headers, f"/sales-orders/{so['id']}/pay",
                      {"account_id": account["id"], "amount": "1500"}, 200)
    assert s_payment["kind"] == "income" and s_payment["status"] == "posted"
    assert s_payment["doc_number"].startswith("ПК-")
    assert s_payment["counterparty_id"] == customer["id"]
    assert s_payment["dimensions"] == {"source_type": "sales_order", "source_id": so["id"]}
    sales_cat = next(c for c in _get(client, admin_headers, "/categories")
                     if c["name"] == "Продажи" and c["kind"] == "income")
    assert s_payment["category_id"] == sales_cat["id"]

    # --- 5. Маржа: выручка 1500 / себестоимость 600 / маржа 900
    report = _get(client, admin_headers, f"/reports/sales?counterparty_id={customer['id']}")
    assert _money(report["shipments"]["revenue_base"]) == Decimal("1500.00")
    assert _money(report["shipments"]["cogs_base"]) == Decimal("600.00")
    assert _money(report["shipments"]["margin_base"]) == Decimal("900.00")
    by_item = next(r for r in report["by_item"] if r["item_id"] == material["id"])
    assert _money(by_item["margin_base"]) == Decimal("900.00")

    # --- 6. Сборка: тех.карта 2 × материал → 1 изделие, себестоимость 200
    card = _post(client, admin_headers, "/tech-cards", {
        "name": f"e2e-сборка-{RUN}", "product_item_id": product["id"], "qty_out": "1",
        "components": [{"item_id": material["id"], "qty": "2"}]})
    prd = _post(client, admin_headers, "/production-orders",
                {"tech_card_id": card["id"], "qty_planned": "1"})
    prd = _post(client, admin_headers, f"/production-orders/{prd['id']}/post", {}, 200)
    assert prd["status"] == "posted" and prd["number"].startswith("СБ-")
    assert _money(prd["material_cost"]) == Decimal("200")  # 2 × 100 по средней

    mat_bal = _get(client, admin_headers, f"/stock/balances?item_id={material['id']}")
    assert _money(mat_bal[0]["qty"]) == Decimal("2")  # 4 − 2 ушло в производство
    prd_state = _get(client, admin_headers, f"/items/{product['id']}")
    assert _money(prd_state["avg_cost"]) == Decimal("200")  # себестоимость = материалы
    prd_bal = _get(client, admin_headers, f"/stock/balances?item_id={product['id']}")
    assert _money(prd_bal[0]["qty"]) == Decimal("1")
    assert _money(prd_bal[0]["value"]) == Decimal("200")

    # --- 7. Цифровые коды: закупка 2 серийников → продажа с FIFO (оба sold)
    codes = [f"E2E-CODE-{RUN}-{i}" for i in (1, 2)]
    d_po = _post(client, admin_headers, "/purchase-orders", {
        "counterparty_id": supplier["id"], "currency": "RUB",
        "lines": [{"item_id": dig_item["id"], "qty": "2", "unit_price": "300"}]})
    _post(client, admin_headers, f"/purchase-orders/{d_po['id']}/confirm", {}, 200)
    d_receipt = _post(client, admin_headers, "/receipts", {
        "purchase_order_id": d_po["id"],
        "lines": [{"item_id": dig_item["id"], "qty": "2", "serial_codes": codes}]})
    d_receipt = _post(client, admin_headers, f"/receipts/{d_receipt['id']}/post", {}, 200)
    assert _qty_on_hand(client, admin_headers, dig_item["id"]) == Decimal("2")

    d_so = _post(client, admin_headers, "/sales-orders", {
        "counterparty_id": customer["id"], "currency": "RUB",
        "lines": [{"item_id": dig_item["id"], "qty": "2", "unit_price": "500"}]})
    _post(client, admin_headers, f"/sales-orders/{d_so['id']}/confirm", {}, 200)
    d_shp = _post(client, admin_headers, "/shipments", {
        "sales_order_id": d_so["id"],
        "lines": [{"item_id": dig_item["id"], "qty": "2"}]})  # коды не заданы → FIFO
    d_shp = _post(client, admin_headers, f"/shipments/{d_shp['id']}/post", {}, 200)
    assert d_shp["status"] == "posted" and d_shp["number"].startswith("ОТ-")

    # оба кода выданы: цифровой склад пуст, расход 2 @ 300 в транзит «Клиент»
    assert _get(client, admin_headers, f"/stock/balances?item_id={dig_item['id']}") == []
    moves = _get(client, admin_headers, f"/stock/moves?item_id={dig_item['id']}")
    issue = [mv for mv in moves if mv["to_location_id"] == locations["Клиент"]["id"]]
    assert len(issue) == 1 and _money(issue[0]["qty"]) == Decimal("2")
    assert _money(issue[0]["unit_cost"]) == Decimal("300")

    # хвост по БД: серийники sold и привязаны к движению выдачи (инвариант §3.2)
    from sqlalchemy import select

    from src.modules.mgmt_accounting.features.inventory import models as inv_m
    from src.modules.mgmt_accounting.features.inventory import service as inv_s

    serials = db.scalars(select(inv_m.ItemSerial).where(
        inv_m.ItemSerial.item_id == uuid.UUID(dig_item["id"]))).all()
    assert len(serials) == 2
    assert all(s.status == "sold" and s.sold_move_id is not None for s in serials)
    assert {inv_s.serial_code(s) for s in serials} == set(codes)

    # маржа цифровых: 1000 − 600 = 400
    report = _get(client, admin_headers, f"/reports/sales?counterparty_id={customer['id']}")
    dig_row = next(r for r in report["by_item"] if r["item_id"] == dig_item["id"])
    assert _money(dig_row["revenue_base"]) == Decimal("1000.00")
    assert _money(dig_row["margin_base"]) == Decimal("400.00")

    # --- 8. Событие сквозного цикла в outbox
    outbox = client.get("/api/v1/events/outbox?event_name=acc.sales.shipped&limit=20",
                        headers=admin_headers)
    assert outbox.status_code == 200
    assert any(e["payload"].get("shipment_id") == d_shp["id"] for e in outbox.json())
