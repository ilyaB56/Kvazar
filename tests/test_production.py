"""Тесты фичи production (resources-core-spec, этап D): тех.карты, заказы
на сборку, списание по средней с учётом резервов, себестоимость продукции
= Σ материалов, сторно сборки.

Цикл приёмки: купил 2 материала → собрал изделия → продал изделие.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.models import EventOutbox
from src.modules.mgmt_accounting import service as acc_service
from src.modules.mgmt_accounting.features.inventory import models as inv
from src.modules.mgmt_accounting.features.inventory import service as inv_service
from src.modules.mgmt_accounting.features.production import service
from src.modules.mgmt_accounting.features.sales import service as sales_service
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


def _admin_id(session) -> uuid.UUID:
    from src.core.models import User

    user = session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    return user.id


def _item(db, name: str, *, kind: str = "physical") -> inv.Item:
    item = inv_service.create_item(db, {
        "sku": f"TST-D-{RUN}-{name}-{uuid.uuid4().hex[:6]}",
        "name": f"pytest D {name}",
        "kind": kind,
        "unit_code": "шт",
    })
    db.commit()
    return item


def _stock(db, user_id, item, qty, unit_cost):
    location = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад"))
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": location.id,
        "lines": [{"item_id": item.id, "qty_fact": qty, "unit_cost": unit_cost}],
    })
    db.commit()


# ---------- Цикл: купил 2 материала → собрал 2 → продал ----------

def test_buy_assemble_sell_cycle(db):
    user_id = _admin_id(db)
    mat1, mat2, product = _item(db, "м1"), _item(db, "м2"), _item(db, "изделие")

    # купили материалы: приёмки закупки (этап B) с разными ценами → средняя
    supplier, _ = acc_service.create_counterparty(db, {"name": f"pytest-D-поставщик-{RUN}"})
    db.commit()
    from src.modules.mgmt_accounting.features.purchasing import service as pur_service
    po = pur_service.create_order(db, user_id=user_id, data={
        "counterparty_id": supplier.id, "currency": "RUB",
        "lines": [
            {"item_id": mat1.id, "qty": Decimal(20), "unit_price": Decimal("30")},
            {"item_id": mat2.id, "qty": Decimal(20), "unit_price": Decimal("5")},
        ],
    })
    pur_service.confirm_order(db, po, user_id=user_id)
    r1 = pur_service.create_receipt(db, user_id=user_id, data={
        "purchase_order_id": po.id,
        "lines": [
            {"item_id": mat1.id, "qty": Decimal(10)},
            {"item_id": mat2.id, "qty": Decimal(20)},
        ],
    })
    pur_service.post_receipt(db, r1)
    db.commit()
    # вторая приёмка mat1 дороже — средняя пересчитывается: (10×30+10×50)/20 = 40
    r2 = pur_service.create_receipt(db, user_id=user_id, data={
        "purchase_order_id": po.id,
        "lines": [{"item_id": mat1.id, "qty": Decimal(10), "unit_cost": Decimal("50")}],
    })
    pur_service.post_receipt(db, r2)
    db.commit()
    assert mat1.avg_cost == Decimal("40.0000")
    assert mat2.avg_cost == Decimal("5.0000")

    # тех.карта: за применение — 1 изделие из 2×mat1 + 4×mat2
    card = service.create_tech_card(db, user_id=user_id, data={
        "name": f"pytest-D-карта-{RUN}",
        "product_item_id": product.id,
        "qty_out": Decimal(1),
        "components": [
            {"item_id": mat1.id, "qty": Decimal(2)},
            {"item_id": mat2.id, "qty": Decimal(4)},
        ],
    })
    db.commit()

    order = service.create_order(db, user_id=user_id, data={
        "tech_card_id": card.id, "qty_planned": Decimal(2),
    })
    service.post_order(db, order)
    db.commit()
    assert order.status == "posted" and order.number.startswith("СБ-")

    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад"))
    # материалы списаны по средней: mat1 20−4=16, mat2 20−8=12
    assert inv_service.location_balance(db, mat1.id, main.id) == Decimal(16)
    assert inv_service.location_balance(db, mat2.id, main.id) == Decimal(12)
    # себестоимость изделия = Σ материалов = 2×40 + 4×5 = 100.0000
    assert product.avg_cost == Decimal("100.0000")
    assert inv_service.location_balance(db, product.id, main.id) == Decimal(2)
    assert order.material_cost == Decimal("200.0000")  # партия 2 шт
    # транзит «Производство» накапливает списанные материалы потоком
    # (как «Клиент» — проданное), в отчёты остатков не попадает

    # продали изделие: маржа = 2×(300 − 100) = 400
    customer, _ = acc_service.create_counterparty(db, {"name": f"pytest-D-клиент-{RUN}"})
    db.commit()
    so = sales_service.create_order(db, user_id=user_id, data={
        "counterparty_id": customer.id,
        "lines": [{"item_id": product.id, "qty": Decimal(2), "unit_price": Decimal("300")}],
    })
    sales_service.confirm_order(db, so, user_id=user_id)
    shp = sales_service.create_shipment(db, user_id=user_id, data={
        "sales_order_id": so.id,
        "lines": [{"item_id": product.id, "qty": Decimal(2)}],
    })
    sales_service.post_shipment(db, shp)
    db.commit()
    report = sales_service.sales_report(db, TODAY, TODAY, customer.id)
    assert report["shipments"]["revenue_base"] == "600.00"
    assert report["shipments"]["cogs_base"] == "200.00"  # 2 × себестоимость 100
    assert report["shipments"]["margin_base"] == "400.00"


# ---------- Резервы: сборка не расходует зарезервированное ----------

def test_component_reservation_blocks_production(db):
    user_id = _admin_id(db)
    mat, product = _item(db, "рез-м"), _item(db, "рез-изд")
    _stock(db, user_id, mat, Decimal(10), Decimal("1"))
    card = service.create_tech_card(db, user_id=user_id, data={
        "name": f"pytest-D-рез-{RUN}",
        "product_item_id": product.id, "qty_out": Decimal(1),
        "components": [{"item_id": mat.id, "qty": Decimal(6)}],
    })
    db.commit()

    # весь остаток зарезервирован заказом продаж — сборке не хватает
    customer, _ = acc_service.create_counterparty(db, {"name": f"pytest-D-рез-клиент-{RUN}"})
    db.commit()
    so = sales_service.create_order(db, user_id=user_id, data={
        "counterparty_id": customer.id,
        "lines": [{"item_id": mat.id, "qty": Decimal(10), "unit_price": Decimal("2")}],
    })
    sales_service.confirm_order(db, so, user_id=user_id)
    db.commit()
    assert sales_service.reserved_qty_by_item(db, mat.id) == Decimal(10)

    order = service.create_order(db, user_id=user_id, data={
        "tech_card_id": card.id, "qty_planned": Decimal(1),
    })
    with pytest.raises(AccountingError, match="insufficient_stock.*reserved"):
        service.post_order(db, order)
        db.commit()
    db.rollback()

    # отмена заказа продаж снимает резерв — сборка проходит
    sales_service.cancel_order(db, so, user_id=user_id)
    db.commit()
    service.post_order(db, order)
    db.commit()
    assert order.status == "posted"


# ---------- Сторно сборки ----------

def test_unpost_order(db):
    user_id = _admin_id(db)
    mat, product = _item(db, "ст-м"), _item(db, "ст-изд")
    _stock(db, user_id, mat, Decimal(10), Decimal("2"))
    card = service.create_tech_card(db, user_id=user_id, data={
        "name": f"pytest-D-сторно-{RUN}",
        "product_item_id": product.id, "qty_out": Decimal(2),
        "components": [{"item_id": mat.id, "qty": Decimal(3)}],
    })
    db.commit()
    order = service.create_order(db, user_id=user_id, data={
        "tech_card_id": card.id, "qty_planned": Decimal(2),
    })
    service.post_order(db, order)
    db.commit()
    main = db.scalar(select(inv.Location).where(inv.Location.name == "Основной склад"))
    assert inv_service.location_balance(db, product.id, main.id) == Decimal(4)  # 2×2
    assert inv_service.location_balance(db, mat.id, main.id) == Decimal(4)  # 10−6

    # продукция продана — сторно запрещено (последующие движения)
    customer, _ = acc_service.create_counterparty(db, {"name": f"pytest-D-ст-клиент-{RUN}"})
    db.commit()
    so = sales_service.create_order(db, user_id=user_id, data={
        "counterparty_id": customer.id,
        "lines": [{"item_id": product.id, "qty": Decimal(4), "unit_price": Decimal("10")}],
    })
    sales_service.confirm_order(db, so, user_id=user_id)
    shp = sales_service.create_shipment(db, user_id=user_id, data={
        "sales_order_id": so.id,
        "lines": [{"item_id": product.id, "qty": Decimal(4)}],
    })
    sales_service.post_shipment(db, shp)
    db.commit()
    with pytest.raises(AccountingError, match="has_subsequent_moves"):
        service.unpost_order(db, order, user_id=user_id, reason="продано")
        db.commit()
    db.rollback()

    # вернули продукцию на склад (сторно продажи) — сторно сборки проходит
    sales_service.unpost_shipment(db, shp, user_id=user_id, reason="возврат")
    db.commit()
    service.unpost_order(db, order, user_id=user_id, reason="брак партии")
    db.commit()
    assert order.is_stornoed is True
    assert inv_service.location_balance(db, product.id, main.id) == Decimal(0)
    assert inv_service.location_balance(db, mat.id, main.id) == Decimal(10)  # материалы вернулись

    # повторное сторно/проведение запрещены
    with pytest.raises(AccountingError, match="already stornoed"):
        service.unpost_order(db, order, user_id=user_id, reason="ещё раз")
        db.commit()
    db.rollback()
    with pytest.raises(AccountingError, match="stornoed"):
        service.post_order(db, order)
        db.commit()
    db.rollback()


# ---------- Гварды и событие ----------

def test_guards_and_event(db):
    user_id = _admin_id(db)
    mat, product = _item(db, "гв-м"), _item(db, "гв-изд")
    service_item = inv_service.create_item(db, {
        "sku": f"TST-D-{RUN}-услуга-{uuid.uuid4().hex[:6]}", "name": "pytest D услуга",
        "kind": "service", "unit_code": "час",
    })
    digital = _item(db, "цифра", kind="digital")
    db.commit()

    # услуга не может быть ни продукцией, ни компонентом
    with pytest.raises(AccountingError, match="product_is_service"):
        service.create_tech_card(db, user_id=user_id, data={
            "name": "x", "product_item_id": service_item.id, "qty_out": Decimal(1),
            "components": [{"item_id": mat.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()
    with pytest.raises(AccountingError, match="component_is_service"):
        service.create_tech_card(db, user_id=user_id, data={
            "name": "x", "product_item_id": product.id, "qty_out": Decimal(1),
            "components": [{"item_id": service_item.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()
    # серийная продукция не поддерживается (v1)
    with pytest.raises(AccountingError, match="product_is_serial"):
        service.create_tech_card(db, user_id=user_id, data={
            "name": "x", "product_item_id": digital.id, "qty_out": Decimal(1),
            "components": [{"item_id": mat.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()
    # без компонентов и компонент = продукция
    with pytest.raises(AccountingError, match="components_required"):
        service.create_tech_card(db, user_id=user_id, data={
            "name": "x", "product_item_id": product.id, "qty_out": Decimal(1),
            "components": [],
        })
        db.commit()
    db.rollback()
    with pytest.raises(AccountingError, match="component_equals_product"):
        service.create_tech_card(db, user_id=user_id, data={
            "name": "x", "product_item_id": product.id, "qty_out": Decimal(1),
            "components": [{"item_id": product.id, "qty": Decimal(1)}],
        })
        db.commit()
    db.rollback()

    # нехватка материала без резервов — insufficient_stock
    _stock(db, user_id, mat, Decimal(1), Decimal("1"))
    card = service.create_tech_card(db, user_id=user_id, data={
        "name": f"pytest-D-гв-{RUN}",
        "product_item_id": product.id, "qty_out": Decimal(1),
        "components": [{"item_id": mat.id, "qty": Decimal(2)}],
    })
    db.commit()
    order = service.create_order(db, user_id=user_id, data={
        "tech_card_id": card.id, "qty_planned": Decimal(1),
    })
    with pytest.raises(AccountingError, match="insufficient_stock"):
        service.post_order(db, order)
        db.commit()
    db.rollback()

    # отмена черновика; повторное проведение и отмена проведённого
    service.cancel_order(db, order, user_id=user_id)
    db.commit()
    assert order.status == "cancelled"
    with pytest.raises(AccountingError, match="Only draft"):
        service.cancel_order(db, order, user_id=user_id)
        db.commit()
    db.rollback()

    # досыпали материала — сборка проходит, событие опубликовано
    inv_service.adjustment(db, user_id=user_id, data={
        "location_id": db.scalar(select(inv.Location).where(
            inv.Location.name == "Основной склад")).id,
        "lines": [{"item_id": mat.id, "qty_fact": Decimal(5)}],
    })
    db.commit()
    order2 = service.create_order(db, user_id=user_id, data={
        "tech_card_id": card.id, "qty_planned": Decimal(1),
    })
    service.post_order(db, order2)
    db.commit()
    with pytest.raises(AccountingError, match="Only draft"):
        service.post_order(db, order2)
        db.commit()
    db.rollback()

    event = db.scalar(select(EventOutbox).where(
        EventOutbox.event_name == "acc.production.order.posted"
    ).order_by(EventOutbox.id.desc()))
    assert event is not None
    payload = event.payload
    assert payload["order_id"] == str(order2.id)
    assert payload["number"].startswith("СБ-")
    assert payload["product_item_id"] == str(product.id)
    assert payload["produced_qty"] == "1.0000"
    # себестоимость изделия = Σ материалов = 2 × средняя 1 = 2.0000
    assert payload["unit_cost"] == "2.0000"
