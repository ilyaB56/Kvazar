"""Тесты фичи sales (resources-core-spec, этап C): цикл заказ → частичные
отгрузки → оплата, продажа цифровых с FIFO-выдачей кодов, контроль минуса,
сделка CRM → черновик заказа, сторно отгрузки, маржа.

Сервисный уровень против dev-БД, по образцу предыдущих фич.
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
from src.modules.mgmt_accounting.features.sales import models as m
from src.modules.mgmt_accounting.features.sales import service
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


def _customer(db, name: str) -> acc.Counterparty:
    counterparty, _ = acc_service.create_counterparty(db, company_id=_co(db), data={"name": name})
    db.commit()
    return counterparty


def _item(db, *, kind: str = "physical") -> inv.Item:
    item = inv_service.create_item(db, company_id=_co(db), data={
        "sku": f"TST-C-{RUN}-{kind}-{uuid.uuid4().hex[:6]}",
        "name": f"pytest C {kind}",
        "kind": kind,
        "unit_code": "лицензия" if kind == "digital" else "шт",
    })
    db.commit()
    return item


def _stock(db, user_id, item, qty, unit_cost, *, location_name="Основной склад"):
    """Заготовка товара: приход инвентаризацией."""
    location = db.scalar(select(inv.Location).where(
        inv.Location.name == location_name, inv.Location.company_id == _co(db)))
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": location.id,
        "lines": [{"item_id": item.id, "qty_fact": qty, "unit_cost": unit_cost}],
    })
    db.commit()
    return location


def _order(db, user_id, counterparty_id, lines, currency="RUB", **extra) -> m.SalesOrder:
    order = service.create_order(db, user_id=user_id, company_id=_co(db), data={
        "counterparty_id": counterparty_id,
        "currency": currency,
        "lines": lines,
        **extra,
    })
    db.commit()
    return order


def _shipment(db, user_id, order, lines) -> m.Shipment:
    shipment = service.create_shipment(db, user_id=user_id, company_id=_co(db), data={
        "sales_order_id": order.id, "lines": lines,
    })
    db.commit()
    return shipment


# ---------- Цикл: заказ → частичные отгрузки → оплата ----------

def test_full_cycle_partial_shipments_payment(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-клиент-{RUN}")
    widget = _item(db)
    _stock(db, user_id, widget, Decimal(10), Decimal("60"))

    order = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_price": Decimal("100")},
    ])
    assert order.status == "draft" and order.amount_base == Decimal("1000.00")
    service.confirm_order(db, order, user_id=user_id)
    db.commit()
    assert order.number and order.number.startswith("ЗК-")
    # резерв v1: подтверждение резервирует строку целиком
    line = db.scalar(select(m.SalesOrderLine).where(m.SalesOrderLine.order_id == order.id))
    assert line.reserved_qty == Decimal(10)

    # отгрузка сверх заказа — 422
    with pytest.raises(AccountingError, match="over_shipment"):
        _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(11)}])
    db.rollback()

    # частичная отгрузка 4: списание по средней (60), статус partially_shipped
    sh1 = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(4)}])
    service.post_shipment(db, sh1)
    db.commit()
    assert sh1.number.startswith("ОТ-") and sh1.status == "posted"
    assert order.status == "partially_shipped"
    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад",
                                     inv.Location.company_id == _co(db)))
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(6)
    assert line.reserved_qty == Decimal(6)
    move = db.scalar(select(inv.StockMove).where(inv.StockMove.source_id == sh1.id))
    assert move.unit_cost == Decimal("60.0000")  # списание по средней (§4)

    # вторая отгрузка 6 → shipped, резерв исчерпан
    sh2 = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(6)}])
    service.post_shipment(db, sh2)
    db.commit()
    assert order.status == "shipped"
    assert line.reserved_qty == Decimal(0)
    assert inv_service.on_hand(db, widget.id) == Decimal(0)

    # оплата: входящая, категория «Продажи» (авто-seed), source-ссылка
    account = acc.Account(company_id=_co(db), name=f"pytest-C-счёт-{RUN}", currency="RUB")
    db.add(account)
    db.flush()
    db.commit()
    txn = service.pay_order(db, order=order, user_id=user_id, data={
        "account_id": account.id, "amount": Decimal("1000"),
    })
    db.commit()
    assert txn.kind == "income" and txn.status == "posted"
    assert txn.counterparty_id == customer.id
    assert txn.dimensions == {"source_type": "sales_order", "source_id": str(order.id)}
    category = db.scalar(select(acc.Category).where(
        acc.Category.name == "Продажи", acc.Category.kind == "income",
        acc.Category.company_id == _co(db)
    ))
    assert category is not None and txn.category_id == category.id


def test_negative_stock_forbidden(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-минус-{RUN}")
    widget = _item(db)
    _stock(db, user_id, widget, Decimal(2), Decimal("10"))

    order = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(5), "unit_price": Decimal("50")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    db.commit()
    sh = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(5)}])
    with pytest.raises(AccountingError, match="insufficient_stock"):
        service.post_shipment(db, sh)
        db.commit()
    db.rollback()
    assert sh.status == "draft"


# ---------- Цифровые товары: FIFO-выдача и явный список ----------

def test_digital_fifo_and_explicit_codes(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-цифра-C-{RUN}")
    code_item = _item(db, kind="digital")
    digital = db.scalar(select(inv.Location).where(inv.Location.name == "Цифровой склад",
                                        inv.Location.company_id == _co(db)))

    # два «пополнения» кодами: инвентаризация серийных — полный факт-список,
    # вторая включает старые коды + новые (иначе старые уйдут в недостачу/void)
    old_codes = [f"C-OLD-{RUN}-{i}" for i in (1, 2)]
    new_codes = [f"C-NEW-{RUN}-{i}" for i in (1, 2)]
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": digital.id,
        "lines": [{"item_id": code_item.id, "serial_codes": old_codes, "unit_cost": Decimal(10)}],
    })
    db.commit()  # отдельная транзакция: now() в PG — время старта транзакции,
    # партии в одной транзакции получили бы одинаковый created_at и FIFO
    # внутри дня потерял бы порядок
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": digital.id,
        "lines": [{"item_id": code_item.id, "serial_codes": old_codes + new_codes}],
    })
    db.commit()

    order = _order(db, user_id, customer.id, [
        {"item_id": code_item.id, "qty": Decimal(4), "unit_price": Decimal("500")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    db.commit()

    # FIFO: без явного списка — старейшие коды
    sh = _shipment(db, user_id, order, [{"item_id": code_item.id, "qty": Decimal(2)}])
    service.post_shipment(db, sh)
    db.commit()
    sold = db.scalars(select(inv.ItemSerial).where(
        inv.ItemSerial.item_id == code_item.id, inv.ItemSerial.status == "sold"
    )).all()
    assert {inv_service.serial_code(s) for s in sold} == set(old_codes)
    assert all(s.sold_move_id is not None for s in sold)
    assert inv_service.location_balance(db, code_item.id, digital.id) == Decimal(2)

    # частичный список без FIFO-добора — 422: список либо полный, либо пуст
    with pytest.raises(AccountingError, match="serial_qty_mismatch"):
        _shipment(db, user_id, order, [
            {"item_id": code_item.id, "qty": Decimal(2),
             "serial_codes": [new_codes[0]]},
        ])
    db.rollback()

    # явный список: выдать конкретные коды (строгое соответствие qty = len)
    sh2 = _shipment(db, user_id, order, [
        {"item_id": code_item.id, "qty": Decimal(2),
         "serial_codes": new_codes},
    ])
    service.post_shipment(db, sh2)
    db.commit()
    moves2 = db.scalars(select(inv.StockMove).where(
        inv.StockMove.source_id == sh2.id
    )).all()
    move_ids = {move.id for move in moves2}
    sold_all = db.scalars(select(inv.ItemSerial).where(
        inv.ItemSerial.item_id == code_item.id, inv.ItemSerial.status == "sold"
    )).all()
    sold_by_sh2 = [s for s in sold_all if s.sold_move_id in move_ids]
    assert {inv_service.serial_code(s) for s in sold_by_sh2} == set(new_codes)
    assert inv_service.on_hand(db, code_item.id) == Decimal(0)

    # кодов меньше остатка — insufficient_stock
    order2 = _order(db, user_id, customer.id, [
        {"item_id": code_item.id, "qty": Decimal(1), "unit_price": Decimal("1")},
    ])
    service.confirm_order(db, order2, user_id=user_id)
    db.commit()
    sh3 = _shipment(db, user_id, order2, [{"item_id": code_item.id, "qty": Decimal(1)}])
    with pytest.raises(AccountingError, match="insufficient_stock"):
        service.post_shipment(db, sh3)
        db.commit()
    db.rollback()


# ---------- Сделка CRM → черновик заказа ----------

def test_deal_prefills_counterparty(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-CRM-клиент-{RUN}")
    widget = _item(db)
    _stock(db, user_id, widget, Decimal(1), Decimal("1"))

    from src.modules.mini_crm import models as crm
    from src.modules.mini_crm import service as crm_service

    stage = db.scalar(select(crm.Stage).where(crm.Stage.name == "Новая"))
    deal = crm_service.create_deal(db, user_id=user_id, data={
        "title": f"pytest-C-сделка-{RUN}",
        "stage_id": stage.id,
        "amount": Decimal("100"), "currency": "RUB",
        "counterparty_id": customer.id,
    })
    db.commit()

    # контрагент префиллится из сделки, crm_deal_id хранится
    order = service.create_order(db, user_id=user_id, company_id=_co(db), data={
        "crm_deal_id": deal.id,
        "lines": [{"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("100")}],
    })
    db.commit()
    assert order.counterparty_id == customer.id
    assert order.crm_deal_id == deal.id

    # несуществующая сделка — 422
    with pytest.raises(AccountingError, match="deal_not_found"):
        service.create_order(db, user_id=user_id, company_id=_co(db), data={
            "crm_deal_id": uuid.uuid4(),
            "lines": [{"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal(1)}],
        })
        db.commit()
    db.rollback()

    # без контрагента и сделки — 422
    with pytest.raises(AccountingError, match="counterparty_id required"):
        service.create_order(db, user_id=user_id, company_id=_co(db), data={
            "lines": [{"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal(1)}],
        })
        db.commit()
    db.rollback()


# ---------- Сторно отгрузки ----------

def test_unpost_shipment_returns_stock_and_serials(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-сторно-C-{RUN}")
    widget = _item(db)
    main = _stock(db, user_id, widget, Decimal(5), Decimal("40"))

    order = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(5), "unit_price": Decimal("80")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    sh = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(3)}])
    service.post_shipment(db, sh)
    db.commit()
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(2)
    assert order.status == "partially_shipped"

    service.unpost_shipment(db, sh, user_id=user_id, reason="возврат")
    db.commit()
    assert sh.is_stornoed is True
    assert inv_service.location_balance(db, widget.id, main.id) == Decimal(5)
    assert order.status == "confirmed"
    # резерв возвращён, количество снова доступно к отгрузке
    line = db.scalar(select(m.SalesOrderLine).where(m.SalesOrderLine.order_id == order.id))
    assert line.reserved_qty == Decimal(5)

    # отгружаем заново после сторно — заказ завершается
    sh2 = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(5)}])
    service.post_shipment(db, sh2)
    db.commit()
    assert order.status == "shipped"

    # повторное сторно/проведение запрещены
    with pytest.raises(AccountingError, match="already stornoed"):
        service.unpost_shipment(db, sh, user_id=user_id, reason="ещё раз")
        db.commit()
    db.rollback()
    with pytest.raises(AccountingError, match="stornoed"):
        service.post_shipment(db, sh)
        db.commit()
    db.rollback()

    # сторно отгрузки с серийниками возвращает коды в in_stock
    code_item = _item(db, kind="digital")
    digital = db.scalar(select(inv.Location).where(inv.Location.name == "Цифровой склад",
                                        inv.Location.company_id == _co(db)))
    codes = [f"C-STORNO-{RUN}-{i}" for i in (1, 2)]
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": digital.id,
        "lines": [{"item_id": code_item.id, "serial_codes": codes, "unit_cost": Decimal(5)}],
    })
    db.commit()
    d_order = _order(db, user_id, customer.id, [
        {"item_id": code_item.id, "qty": Decimal(2), "unit_price": Decimal("50")},
    ])
    service.confirm_order(db, d_order, user_id=user_id)
    d_sh = _shipment(db, user_id, d_order, [{"item_id": code_item.id, "qty": Decimal(2)}])
    service.post_shipment(db, d_sh)
    db.commit()
    service.unpost_shipment(db, d_sh, user_id=user_id, reason="код не использован")
    db.commit()
    serials = db.scalars(select(inv.ItemSerial).where(
        inv.ItemSerial.item_id == code_item.id
    )).all()
    assert all(s.status == "in_stock" for s in serials)
    assert all(s.location_id == digital.id for s in serials)
    assert all(s.sold_move_id is None for s in serials)


# ---------- Маржа и события ----------

def test_sales_report_margin_and_events(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-маржа-{RUN}")
    widget = _item(db)
    _stock(db, user_id, widget, Decimal(10), Decimal("60"))  # себестоимость 60

    order = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(10), "unit_price": Decimal("100")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    sh = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(10)}])
    service.post_shipment(db, sh)
    db.commit()

    report = service.sales_report(db, TODAY, TODAY, customer.id)
    # выручка 10×100 = 1000, себестоимость 10×60 = 600, маржа 400
    row = next(r for r in report["by_counterparty"]
               if r["counterparty_id"] == str(customer.id))
    assert row["revenue_base"] == "1000.00"
    assert row["cogs_base"] == "600.00"
    assert row["margin_base"] == "400.00"
    by_item = next(r for r in report["by_item"] if r["item_id"] == str(widget.id))
    assert by_item["margin_base"] == "400.00"
    assert report["shipments"]["margin_base"] == "400.00"

    # сторноутая отгрузка из маржи исчезает
    service.unpost_shipment(db, sh, user_id=user_id, reason="отчёт без сторно")
    db.commit()
    report = service.sales_report(db, TODAY, TODAY, customer.id)
    assert report["shipments"]["revenue_base"] == "0.00"
    assert report["shipments"]["margin_base"] == "0.00"

    def payload(event: str):
        rows = db.scalars(select(EventOutbox).where(
            EventOutbox.event_name == event
        ).order_by(EventOutbox.id.desc())).all()
        return rows[0].payload if rows else None

    created = payload("acc.sales.order.created")
    assert created and created["amount"] == "1000.0000"
    confirmed = payload("acc.sales.order.confirmed")
    assert confirmed and confirmed["number"].startswith("ЗК-")
    shipped = payload("acc.sales.shipped")
    assert shipped and shipped["sales_order_id"] == str(order.id)
    assert shipped["revenue_base"] in ("1000.00", "0.00")


# ---------- Отмена и прочие гварды ----------

def test_cancel_and_guards(db):
    user_id = _admin_id(db)
    customer = _customer(db, f"pytest-отмена-C-{RUN}")
    widget = _item(db)
    _stock(db, user_id, widget, Decimal(1), Decimal("1"))

    draft = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("10")},
    ])
    service.cancel_order(db, draft, user_id=user_id)
    db.commit()
    assert draft.status == "cancelled"

    order = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("10")},
    ])
    service.confirm_order(db, order, user_id=user_id)
    sh = _shipment(db, user_id, order, [{"item_id": widget.id, "qty": Decimal(1)}])
    service.post_shipment(db, sh)
    db.commit()
    # отмена с отгрузкой запрещена (бизнес-проверка первична)
    with pytest.raises(AccountingError, match="order_has_shipments"):
        service.cancel_order(db, order, user_id=user_id)
        db.commit()
    db.rollback()

    # отмена возвращает резерв
    order2 = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("10")},
    ])
    service.confirm_order(db, order2, user_id=user_id)
    db.commit()
    line2 = db.scalar(select(m.SalesOrderLine).where(m.SalesOrderLine.order_id == order2.id))
    assert line2.reserved_qty == Decimal(1)
    service.cancel_order(db, order2, user_id=user_id)
    db.commit()
    assert line2.reserved_qty == Decimal(0)

    # черновик не оплачивается
    draft2 = _order(db, user_id, customer.id, [
        {"item_id": widget.id, "qty": Decimal(1), "unit_price": Decimal("10")},
    ])
    account = db.scalar(select(acc.Account).where(
        acc.Account.name == f"pytest-C-счёт-{RUN}"
    ))
    with pytest.raises(AccountingError, match="order_not_payable"):
        service.pay_order(db, order=draft2, user_id=user_id, data={
            "account_id": account.id, "amount": Decimal("1"),
        })
        db.commit()
    db.rollback()

    # отгрузка по черновику невозможна
    with pytest.raises(AccountingError, match="order_not_shippable"):
        service.create_shipment(db, user_id=user_id, company_id=_co(db), data={
            "sales_order_id": draft2.id,
            "lines": [{"item_id": widget.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()
