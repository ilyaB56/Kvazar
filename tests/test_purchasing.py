"""Тесты фичи purchasing (resources-core-spec, этап B): заказы, приёмки
(частичные), валютная закупка с заморозкой курса, сторно приёмки, оплаты
и взаиморасчёты, события.

Сервисный уровень против dev-БД, по образцу test_inventory.py.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.models import EventOutbox
from src.modules.mgmt_accounting import models as acc
from src.modules.mgmt_accounting import service as acc_service
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service
from src.modules.mgmt_accounting.features.purchasing import models as m
from src.modules.mgmt_accounting.features.purchasing import service
from src.modules.mgmt_accounting.service import AccountingError

RUN = uuid.uuid4().hex[:8]
TODAY = date.today()


@pytest.fixture(scope="module")
def db():
    from src.db import SessionLocal, engine

    try:
        engine.connect().close()
    except Exception:
        pytest.skip("PostgreSQL not available")
    session = SessionLocal()
    yield session
    session.close()


def _co(session) -> uuid.UUID:
    """Компания теста — «Основная» (multitenancy B1: company_id обязателен)."""
    from src.core.models import Company

    company = session.scalar(select(Company).where(Company.name == "Основная"))
    assert company is not None, "run migrations (0027)"
    return company.id


def _admin_id(session) -> uuid.UUID:
    from src.core.models import User

    user = session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    return user.id


def _supplier(db, name: str) -> acc.Counterparty:
    counterparty, _ = acc_service.create_counterparty(db, company_id=_co(db), data={"name": name})
    db.commit()
    return counterparty


def _item(db, *, kind: str = "physical") -> inv.Item:
    item = inv_service.create_item(db, company_id=_co(db), data={
        "sku": f"TST-B-{RUN}-{kind}-{uuid.uuid4().hex[:6]}",
        "name": f"pytest B {kind}",
        "kind": kind,
        "unit_code": "лицензия" if kind == "digital" else "шт",
    })
    db.commit()
    return item


def _order(db, user_id, counterparty_id, lines, currency="RUB") -> m.PurchaseOrder:
    order = service.create_order(db, user_id=user_id, company_id=_co(db), data={
        "counterparty_id": counterparty_id,
        "currency": currency,
        "lines": lines,
    })
    db.commit()
    return order


def _receipt(db, user_id, order, lines) -> m.Receipt:
    receipt = service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
        "purchase_order_id": order.id, "lines": lines,
    })
    db.commit()
    return receipt


# ---------- Заказы ----------

def test_order_flow_numbers_and_freeze(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-поставщик-{RUN}")
    widget = _item(db)
    service_item = inv_service.create_item(db, company_id=_co(db), data={
        "sku": f"TST-B-{RUN}-svc-{uuid.uuid4().hex[:6]}", "name": "pytest B услуга",
        "kind": "service", "unit_code": "час",
    })
    db.commit()

    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_price": Decimal("100")},
        {"item_id": service_item.id, "qty": Decimal(2), "unit_price": Decimal("500")},
    ])
    assert order.status == "draft" and order.number is None
    assert order.amount == Decimal("2000.0000")
    assert order.amount_base == Decimal("2000.00")
    assert order.rate == Decimal(1)

    service.confirm_order(db, order, user_id=user_id)
    db.commit()
    assert order.status == "confirmed"
    assert order.number and order.number.startswith("ЗП-")

    # повторный confirm и отмена с приёмкой запрещены (последняя — ниже)
    with pytest.raises(AccountingError, match="Only draft"):
        service.confirm_order(db, order, user_id=user_id)
        db.commit()
    db.rollback()

    # пустой заказ и дубль — ошибки создания
    with pytest.raises(AccountingError, match="order_lines_required"):
        _order(db, user_id, supplier.id, [])
    with pytest.raises(AccountingError):
        _order(db, user_id, uuid.uuid4(), [
            {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal(1)}
        ])


def test_currency_order_freezes_rate_at_creation(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-валюта-{RUN}")
    gadget = _item(db)
    acc_service.upsert_rate(db, TODAY, "USD", Decimal("90.50000000"))
    db.commit()

    order = _order(db, user_id, supplier.id, [
        {"item_id": gadget.id, "qty": Decimal(5), "unit_price": Decimal(20)},
    ], currency="USD")
    assert order.rate == Decimal("90.50000000")
    assert order.amount == Decimal("100.0000")
    # 100 USD × 90.5 = 9050.00 (half-up, ADR-003)
    assert order.amount_base == Decimal("9050.00")

    # курс после создания не влияет: заморозка на момент создания (§2.6)
    acc_service.upsert_rate(db, TODAY, "USD", Decimal("99"))
    db.commit()
    service.confirm_order(db, order, user_id=user_id)
    db.commit()
    assert order.rate == Decimal("90.50000000")


# ---------- Приёмки: частичные, средняя, статусы ----------

def test_full_cycle_partial_receipts_and_avg_cost(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-цикл-{RUN}")
    widget = _item(db)
    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_price": Decimal("100")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    db.commit()

    # частичная приёмка 4: движение Поставщик → Основной, avg = 100
    receipt1 = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(4), "unit_cost": Decimal("100")},
    ])
    service.post_receipt(db, receipt1)
    db.commit()
    assert receipt1.status == "posted"
    assert receipt1.number and receipt1.number.startswith("ПМ-")
    assert order.status == "partially_received"
    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад",
                                     inv.Location.company_id == _co(db)))
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(4)
    assert widget.avg_cost == Decimal("100.0000")

    # приёмка сверх остатка по строке заказа (осталось 6) — 422
    with pytest.raises(AccountingError, match="over_receipt"):
        _receipt(db, user_id, order, [
            {"item_id": widget.id, "qty": Decimal(7), "unit_cost": Decimal("1")},
        ])
    db.rollback()

    # отмена заказа с принятым товаром запрещена (статус partially_received)
    with pytest.raises(AccountingError, match="order_has_receipts"):
        service.cancel_order(db, order, user_id=user_id)
        db.commit()
    db.rollback()

    # вторая приёмка по цене 150: средняя = (4×100 + 6×150)/10 = 130
    receipt2 = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(6), "unit_cost": Decimal("150")},
    ])
    service.post_receipt(db, receipt2)
    db.commit()
    assert order.status == "received"
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(10)
    assert widget.avg_cost == Decimal("130.0000")

    # полностью принятый заказ новых приёмок не принимает
    with pytest.raises(AccountingError, match="order_not_confirmable"):
        _receipt(db, user_id, order, [
            {"item_id": widget.id, "qty": Decimal(1), "unit_cost": Decimal("1")},
        ])
    db.rollback()

    # услуга не принимается на склад
    svc_item = inv_service.create_item(db, company_id=_co(db), data={
        "sku": f"TST-B-{RUN}-svc2-{uuid.uuid4().hex[:6]}", "name": "pytest B услуга 2",
        "kind": "service", "unit_code": "час",
    })
    db.commit()
    with pytest.raises(AccountingError, match="service_item_not_receivable"):
        service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
            "counterparty_id": supplier.id,
            "lines": [{"item_id": svc_item.id, "qty": Decimal(1), "unit_cost": Decimal(1)}],
        })
        db.commit()
    db.rollback()


def test_currency_receipt_cost_from_frozen_rate(db):
    """Валютная закупка: себестоимость приёмки по умолчанию = цена ×
    замороженный курс заказа (§3.3); средняя — в базовой валюте."""
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-валютная-приёмка-{RUN}")
    gadget = _item(db)
    acc_service.upsert_rate(db, TODAY, "EUR", Decimal("100.00000000"))
    db.commit()
    order = _order(db, user_id, supplier.id, [
        {"item_id": gadget.id, "qty": Decimal(2), "unit_price": Decimal("10")},
    ], currency="EUR")
    service.confirm_order(db, order, user_id=user_id)
    db.commit()

    # unit_cost не передан → 10 EUR × 100 = 1000 базовой
    receipt = service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
        "purchase_order_id": order.id,
        "lines": [{"item_id": gadget.id, "qty": Decimal(2)}],
    })
    db.commit()
    line = db.scalar(select(m.ReceiptLine).where(m.ReceiptLine.receipt_id == receipt.id))
    assert line.unit_cost == Decimal("1000.00")
    service.post_receipt(db, receipt)
    db.commit()
    assert gadget.avg_cost == Decimal("1000.0000")


# ---------- Сторно приёмки ----------

def test_unpost_receipt_reverses_moves(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-сторно-{RUN}")
    widget = _item(db)
    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад",
                                     inv.Location.company_id == _co(db)))
    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_price": Decimal("50")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    receipt = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(5), "unit_cost": Decimal("50")},
    ])
    service.post_receipt(db, receipt)
    db.commit()
    assert order.status == "partially_received"
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(5)

    # товар уже перемещён — сторно запрещено (последующие движения)
    second = inv_service.create_location(db, company_id=_co(db), data={"name": f"pytest-B-склад-{RUN}", "kind": "physical"})
    db.commit()
    inv_service.transfer_stock(db, user_id=user_id, data={
        "item_id": widget.id, "qty": Decimal(2),
        "from_location_id": main.id, "to_location_id": second.id,
    })
    db.commit()
    with pytest.raises(AccountingError, match="has_subsequent_moves"):
        service.unpost_receipt(db, receipt, user_id=user_id, reason="тест")
        db.commit()
    db.rollback()

    # вернули товар на склад — сторно проходит: баланс 0, статус заказа назад
    inv_service.transfer_stock(db, user_id=user_id, data={
        "item_id": widget.id, "qty": Decimal(2),
        "from_location_id": second.id, "to_location_id": main.id,
    })
    db.commit()
    service.unpost_receipt(db, receipt, user_id=user_id, reason="брак поставщика")
    db.commit()
    assert receipt.is_stornoed is True
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(0)
    assert inv_service.on_hand(db, widget.id) == Decimal(0)
    assert order.status == "confirmed"

    # повторное сторно и повторное проведение запрещены
    with pytest.raises(AccountingError, match="already stornoed"):
        service.unpost_receipt(db, receipt, user_id=user_id, reason="ещё раз")
        db.commit()
    db.rollback()
    with pytest.raises(AccountingError, match="stornoed"):
        service.post_receipt(db, receipt)
        db.commit()
    db.rollback()

    # сторноутая приёмка не считается принятым количеством: новая приёмка на 10
    receipt2 = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_cost": Decimal("50")},
    ])
    service.post_receipt(db, receipt2)
    db.commit()
    assert order.status == "received"


def test_unpost_receipt_aggregates_same_item_lines(db):
    """Строгий unpost (этап E): строки одного (товар, склад) суммируются —
    построчная проверка пропускала случай 8+8 при остатке 11."""
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-агр-{RUN}")
    widget = _item(db)
    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад",
                                     inv.Location.company_id == _co(db)))

    base = service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
        "counterparty_id": supplier.id,
        "lines": [{"item_id": widget.id, "qty": Decimal(15), "unit_cost": Decimal("1")}],
    })
    service.post_receipt(db, base)
    db.commit()

    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(16), "unit_price": Decimal("1")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    receipt = service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
        "purchase_order_id": order.id,
        "lines": [
            {"item_id": widget.id, "qty": Decimal(8)},
            {"item_id": widget.id, "qty": Decimal(8)},
        ],
    })
    service.post_receipt(db, receipt)
    db.commit()
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(31)

    second = inv_service.create_location(db, company_id=_co(db), data={"name": f"pytest-агр-склад-{RUN}", "kind": "physical"})
    db.commit()
    inv_service.transfer_stock(db, user_id=user_id, data={
        "item_id": widget.id, "qty": Decimal(20),
        "from_location_id": main.id, "to_location_id": second.id,
    })
    db.commit()  # остаток 11 < 16 суммарно (но каждая строка ≤ 11)

    with pytest.raises(AccountingError, match="has_subsequent_moves"):
        service.unpost_receipt(db, receipt, user_id=user_id, reason="агрегат")
        db.commit()
    db.rollback()

    inv_service.transfer_stock(db, user_id=user_id, data={
        "item_id": widget.id, "qty": Decimal(20),
        "from_location_id": second.id, "to_location_id": main.id,
    })
    db.commit()
    service.unpost_receipt(db, receipt, user_id=user_id, reason="вернули товар")
    db.commit()
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(15)


# ---------- Оплаты и взаиморасчёты ----------

def test_payments_category_source_and_balance(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-оплата-{RUN}")
    widget = _item(db)
    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(4), "unit_price": Decimal("250")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    receipt = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(4), "unit_cost": Decimal("250")},
    ])
    service.post_receipt(db, receipt)
    db.commit()

    # до оплаты — должны 1000
    balance = service.counterparty_balance(db, supplier.id)
    assert balance["received_amount_base"] == "1000.00"
    assert balance["paid_amount_base"] == "0.00"
    assert balance["balance"] == "1000.00"
    assert any(d["kind"] == "receipt" and d["number"] == receipt.number
               for d in balance["documents"])

    account = acc.Account(company_id=_co(db), name=f"pytest-B-счёт-{RUN}", currency="RUB")
    db.add(account)
    db.flush()
    db.commit()

    # частичная оплата 400: категория «Закупки товаров» (авто-seed), контрагент
    txn1 = service.pay_order(db, order=order, user_id=user_id, data={
        "account_id": account.id, "amount": Decimal("400"),
    })
    db.commit()
    assert txn1.status == "posted" and txn1.kind == "expense"
    assert txn1.counterparty_id == supplier.id
    assert txn1.dimensions == {"source_type": "purchase_order", "source_id": str(order.id)}
    category = db.scalar(select(acc.Category).where(
        acc.Category.name == "Закупки товаров", acc.Category.kind == "expense",
        acc.Category.company_id == _co(db)
    ))
    assert category is not None and txn1.category_id == category.id
    # категория одна на две оплаты (авто-seed идемпотентен)
    txn2 = service.pay_order(db, order=order, user_id=user_id, data={
        "account_id": account.id, "amount": Decimal("600"),
    })
    db.commit()
    assert txn2.category_id == category.id

    balance = service.counterparty_balance(db, supplier.id)
    assert balance["paid_amount_base"] == "1000.00"
    assert balance["balance"] == "0.00"
    payments = [d for d in balance["documents"] if d["kind"] == "payment"]
    assert len(payments) == 2

    # заказ в draft не оплачивается
    draft = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("1")},
    ])
    with pytest.raises(AccountingError, match="order_not_payable"):
        service.pay_order(db, order=draft, user_id=user_id, data={
            "account_id": account.id, "amount": Decimal("1"),
        })
        db.commit()
    db.rollback()


def test_purchases_report_aggregates(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-отчёт-{RUN}")
    widget = _item(db)
    order = _order(db, user_id, supplier.id, [
        {"item_id": widget.id, "qty": Decimal(3), "unit_price": Decimal("10")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    receipt = _receipt(db, user_id, order, [
        {"item_id": widget.id, "qty": Decimal(2), "unit_cost": Decimal("10")},
    ])
    service.post_receipt(db, receipt)
    db.commit()
    account = acc.Account(company_id=_co(db), name=f"pytest-B-отчёт-счёт-{RUN}", currency="RUB")
    db.add(account)
    db.flush()
    service.pay_order(db, order=order, user_id=user_id, data={
        "account_id": account.id, "amount": Decimal("20"),
    })
    db.commit()

    report = service.purchases_report(db, TODAY, TODAY, None)
    my = [row for row in report["by_counterparty"] if row["counterparty_id"] == str(supplier.id)]
    assert my and my[0]["orders_amount_base"] == "30.00"
    assert my[0]["received_amount_base"] == "20.00"
    assert my[0]["paid_amount_base"] == "20.00"
    assert my[0]["balance"] == "0.00"
    by_item = [row for row in report["by_item"] if row["item_id"] == str(widget.id)]
    assert by_item and by_item[0]["qty"] == "2.0000" and by_item[0]["amount_base"] == "20.00"

    report_cp = service.purchases_report(db, TODAY, TODAY, supplier.id)
    assert len(report_cp["by_counterparty"]) == 1

    # сторноутая приёмка в отчёт не попадает
    service.unpost_receipt(db, receipt, user_id=user_id, reason="отчёт без сторно")
    db.commit()
    report = service.purchases_report(db, TODAY, TODAY, supplier.id)
    assert report["receipts"]["count"] == 0
    assert report["receipts"]["amount_base"] == "0.00"


# ---------- Серийники через приёмку и события ----------

def test_receipt_registers_serials_and_events(db):
    user_id = _admin_id(db)
    supplier = _supplier(db, f"pytest-серийники-B-{RUN}")
    digital = _item(db, kind="digital")
    order = _order(db, user_id, supplier.id, [
        {"item_id": digital.id, "qty": Decimal(2), "unit_price": Decimal("300")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    codes = [f"B-SERIAL-{RUN}-1", f"B-SERIAL-{RUN}-2"]
    receipt = service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
        "purchase_order_id": order.id,
        "lines": [{"item_id": digital.id, "qty": Decimal(2), "serial_codes": codes}],
    })
    service.post_receipt(db, receipt)
    db.commit()

    serials = db.scalars(select(inv.ItemSerial).where(
        inv.ItemSerial.item_id == digital.id
    )).all()
    assert len(serials) == 2
    assert all(s.status == "in_stock" for s in serials)
    assert {inv_service.serial_code(s) for s in serials} == set(codes)

    # qty без кодов — 422
    with pytest.raises(AccountingError, match="serial_qty_mismatch"):
        service.create_receipt(db, user_id=user_id, company_id=_co(db), data={
            "counterparty_id": supplier.id,
            "lines": [{"item_id": digital.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()

    def payload(event: str):
        rows = db.scalars(select(EventOutbox).where(
            EventOutbox.event_name == event
        ).order_by(EventOutbox.id.desc())).all()
        return rows[0].payload if rows else None

    created = payload("acc.purchase.order.created")
    assert created and created["counterparty_id"] == str(supplier.id)
    assert created["amount"] == "600.0000" and created["amount_base"] == "600.00"
    confirmed = payload("acc.purchase.order.confirmed")
    assert confirmed and confirmed["number"].startswith("ЗП-")
    received = payload("acc.purchase.received")
    assert received and received["receipt_id"] == str(receipt.id)
    assert received["amount_base"] == "600.00"
    # движения приёмки дали батч stock_changed
    changed = payload("acc.inventory.stock_changed")
    assert changed and changed["source_type"] == "receipt"
