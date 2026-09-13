"""Тесты фичи inventory (resources-core-spec, этап A): двойная запись,
совместимость физических/цифровых локаций, серийники (Fernet), средняя
себестоимость, insufficient_stock, события, правила НСИ.

DB-тесты идут против dev-БД (docker compose up); уникальный суффикс в sku
защищает от повторных запусков. Проверки сервисного слоя — по образцу
tests/test_accounting.py.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core import crypto
from src.core.models import EventOutbox
from src.modules.mgmt_accounting.features.inventory import models as m
from src.modules.mgmt_accounting.features.inventory import service
from src.modules.mgmt_accounting.service import AccountingError

RUN = uuid.uuid4().hex[:8]


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
    assert user is not None, "seed admin not found (python -m src.seed)"
    return user.id


def _location(session, name: str) -> m.Location:
    return session.scalar(select(m.Location).where(
        m.Location.name == name, m.Location.company_id == _co(session)))


def _make_item(session, *, kind: str = "physical", tracking: str | None = None,
               threshold: Decimal | None = None) -> m.Item:
    item = service.create_item(session, company_id=_co(session), data={
        "sku": f"TST-{RUN}-{kind}-{uuid.uuid4().hex[:6]}",
        "name": f"pytest {kind}",
        "kind": kind,
        "unit_code": "лицензия" if kind == "digital" else "шт",
        **({"tracking": tracking} if tracking else {}),
        **({"low_stock_threshold": threshold} if threshold is not None else {}),
    })
    session.commit()
    return item


def _adjust(session, user_id, location, lines) -> list[m.StockMove]:
    moves = service.adjustment(
        session, user_id=user_id,
        data={"location_id": location.id, "lines": lines},
    )
    session.commit()
    return moves


def _balance(session, item, location) -> Decimal:
    return service.location_balance(session, item.id, location.id)


# ---------- Расчёты (без БД) ----------

def test_avg_cost_weighted_formula():
    # расчёт без БД: company_id — любая (NOT NULL в модели)
    item = m.Item(company_id=uuid.uuid4(), sku="x", name="x", kind="physical",
                  unit_code="шт", tracking="qty", avg_cost=Decimal("100"))
    # 10 rest @ 100 + 10 in @ 200 → 150
    service.recalc_avg_cost(item, Decimal(10), Decimal(10), Decimal(200))
    assert item.avg_cost == Decimal("150.0000")
    # дробный приход: 3 @ 100 + 1 @ 200 → 325/4 = 81.25
    item.avg_cost = Decimal("100")
    service.recalc_avg_cost(item, Decimal(3), Decimal(1), Decimal("125"))
    assert item.avg_cost == Decimal("106.2500")
    # первый приход (avg NULL) — цена прихода
    item.avg_cost = None
    service.recalc_avg_cost(item, Decimal(0), Decimal(5), Decimal("123.4567"))
    assert item.avg_cost == Decimal("123.4567")


def test_serial_hash_is_sha256():
    assert service._serial_hash(" ABC ") == hashlib.sha256(b"ABC").hexdigest()


# ---------- Двойная запись и совместимость (PostgreSQL) ----------

def test_double_entry_receipt_transfer_writeoff(db):
    user_id = _admin_id(db)
    item = _make_item(db)
    main = _location(db, "Основной склад")
    second = service.create_location(db, company_id=_co(db), data={"name": f"pytest-склад-{RUN}", "kind": "physical"})
    db.commit()

    # приход 10 @ 100 (первый — цена явная), затем перемещение 4
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(10),
                                 "unit_cost": Decimal(100)}])
    service.transfer_stock(db, user_id=user_id, data={
        "item_id": item.id, "qty": Decimal(4),
        "from_location_id": main.id, "to_location_id": second.id,
    })
    db.commit()

    assert _balance(db, item, main) == Decimal(6)
    assert _balance(db, item, second) == Decimal(4)
    assert service.on_hand(db, item.id) == Decimal(10)

    # каждая операция — одна строка from → to (двойная запись §2.2)
    moves = service.stock_moves(db, item_id=item.id)
    assert [(str(x.from_location_id), str(x.to_location_id)) for x in moves] == [
        (str(_location(db, "Брак").id), str(main.id)),
        (str(main.id), str(second.id)),
    ]
    # списание 3 (факт 3 на основном)
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(3)}])
    assert _balance(db, item, main) == Decimal(3)
    assert service.on_hand(db, item.id) == Decimal(7)


def test_kind_mismatch_physical_on_digital(db):
    user_id = _admin_id(db)
    item = _make_item(db, kind="physical")
    digital = _location(db, "Цифровой склад")
    with pytest.raises(AccountingError, match="kind_mismatch"):
        _adjust(db, user_id, digital, [{"item_id": item.id, "qty_fact": Decimal(1),
                                        "unit_cost": Decimal(10)}])


def test_kind_mismatch_digital_on_physical(db):
    user_id = _admin_id(db)
    item = _make_item(db, kind="digital")
    main = _location(db, "Основной склад")
    with pytest.raises(AccountingError, match="kind_mismatch"):
        _adjust(db, user_id, main, [{"item_id": item.id,
                                     "serial_codes": ["X1"], "unit_cost": Decimal(10)}])


def test_service_item_has_no_moves(db):
    user_id = _admin_id(db)
    item = _make_item(db, kind="service")
    main = _location(db, "Основной склад")
    with pytest.raises(AccountingError, match="service_items_have_no_stock"):
        _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(1),
                                     "unit_cost": Decimal(10)}])


def test_insufficient_stock_and_issue_cost(db):
    user_id = _admin_id(db)
    item = _make_item(db)
    main = _location(db, "Основной склад")
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(6),
                                 "unit_cost": Decimal(100)}])
    second = service.create_location(db, company_id=_co(db), data={"name": f"pytest-склад2-{RUN}", "kind": "physical"})
    db.commit()

    with pytest.raises(AccountingError, match="insufficient_stock"):
        service.transfer_stock(db, user_id=user_id, data={
            "item_id": item.id, "qty": Decimal(7),
            "from_location_id": main.id, "to_location_id": second.id,
        })
        db.commit()

    # перемещение сверх остатка запрещено; в пределах — по avg_cost (§4)
    move = service.transfer_stock(db, user_id=user_id, data={
        "item_id": item.id, "qty": Decimal(2),
        "from_location_id": main.id, "to_location_id": second.id,
    })
    db.commit()
    assert move.unit_cost == Decimal("100.0000")
    assert item.avg_cost == Decimal("100.0000")  # расход среднюю не меняет


def test_avg_cost_flow_first_receipt_and_value(db):
    user_id = _admin_id(db)
    item = _make_item(db)
    main = _location(db, "Основной склад")
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(10),
                                 "unit_cost": Decimal("123.4")}])
    assert item.avg_cost == Decimal("123.4000")
    rows = [r for r in service.stock_balances(db) if r["item_id"] == str(item.id)]
    assert rows and rows[0]["qty"] == "10.0000"
    assert rows[0]["avg_cost"] == "123.4000"
    assert rows[0]["value"] == "1234.0000"

    # излишек при существующей avg — по avg, переданная цена игнорируется (§4)
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(12),
                                 "unit_cost": Decimal(999)}])
    assert item.avg_cost == Decimal("123.4000")
    assert _balance(db, item, main) == Decimal(12)

    # срез остатков на дату: вчера движений не было
    from datetime import date, timedelta

    yesterday = date.today() - timedelta(days=1)
    assert service.stock_balances(db, on_date=yesterday, item_id=item.id) == []


# ---------- Серийники (Fernet, §11.1) ----------

def test_serials_receipt_transfer_writeoff_encrypted(db):
    user_id = _admin_id(db)
    item = _make_item(db, kind="digital")  # tracking по умолчанию serial
    assert item.tracking == "serial"
    digital = _location(db, "Цифровой склад")

    codes = [f"TOPUP-{RUN}-{i}" for i in range(3)]
    _adjust(db, user_id, digital, [{"item_id": item.id, "serial_codes": codes,
                                    "unit_cost": Decimal(50)}])
    serials = db.scalars(select(m.ItemSerial).where(m.ItemSerial.item_id == item.id)
                         .order_by(m.ItemSerial.received_at)).all()
    assert len(serials) == 3
    for row, code in zip(serials, codes, strict=False):
        assert row.status == "in_stock"
        assert row.location_id == digital.id
        # актив хранится шифрованным: raw-код не встречается в шифротексте,
        # расшифровка восстанавливает код, отпечаток — sha256
        assert code not in row.code_enc
        assert service.serial_code(row) == code
        assert row.code_hash == hashlib.sha256(code.encode()).hexdigest()

    # перемещение серийника: qty=1 + код
    digital2 = service.create_location(db, company_id=_co(db), data={"name": f"pytest-цифра-{RUN}", "kind": "digital"})
    db.commit()
    move = service.transfer_stock(db, user_id=user_id, data={
        "item_id": item.id, "qty": Decimal(1),
        "from_location_id": digital.id, "to_location_id": digital2.id,
        "serial_codes": [codes[0]],
    })
    db.commit()
    assert move.qty == Decimal(1)
    moved = db.scalar(select(m.ItemSerial).where(
        m.ItemSerial.item_id == item.id,
        m.ItemSerial.code_hash == hashlib.sha256(codes[0].encode()).hexdigest(),
    ))
    assert moved.location_id == digital2.id

    # код с другой локации в факте — serial_already_exists (экземпляр один)
    with pytest.raises(AccountingError, match="serial_already_exists"):
        _adjust(db, user_id, digital2, [{"item_id": item.id,
                                         "serial_codes": [codes[0], codes[1]]}])

    # неизвестный код — serial_not_found; qty без кода — serial_qty_mismatch
    with pytest.raises(AccountingError, match="serial_not_found"):
        service.transfer_stock(db, user_id=user_id, data={
            "item_id": item.id, "qty": Decimal(1),
            "from_location_id": digital.id, "to_location_id": digital2.id,
            "serial_codes": ["NO-SUCH-CODE"],
        })
    with pytest.raises(AccountingError, match="serial_qty_mismatch"):
        service.transfer_stock(db, user_id=user_id, data={
            "item_id": item.id, "qty": Decimal(2),
            "from_location_id": digital.id, "to_location_id": digital2.id,
            "serial_codes": [codes[1]],
        })

    # инвентаризация серийных: факт = список кодов; пропавшие → void
    _adjust(db, user_id, digital2, [{"item_id": item.id, "serial_codes": []}])
    assert moved.status == "void" and moved.location_id is None
    # на цифровом складе остались codes[1], codes[2]
    assert _balance(db, item, digital) == Decimal(2)
    assert service.on_hand(db, item.id) == Decimal(2)


def test_crypto_roundtrip_and_key_source():
    # серийники шифруются тем же ключом, что credentials подключений (§11.1)
    blob = crypto.encrypt_str("secret-code")
    assert "secret-code" not in blob
    assert crypto.decrypt_str(blob) == "secret-code"


# ---------- События и НСИ ----------

def test_events_stock_changed_and_low_stock(db):
    user_id = _admin_id(db)
    item = _make_item(db, threshold=Decimal(3))
    main = _location(db, "Основной склад")

    def outbox(event_name: str) -> list[dict]:
        return db.scalars(select(EventOutbox).where(
            EventOutbox.event_name == event_name,
        ).order_by(EventOutbox.id)).all()

    def fresh(event_name: str) -> list[dict]:
        # отфильтровать строки этой сессии по item_id в payload
        rows = []
        for row in outbox(event_name):
            payload = row.payload or {}
            text = str(payload)
            if str(item.id) in text:
                rows.append(payload)
        return rows

    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(10),
                                 "unit_cost": Decimal(5)}])
    changed = fresh("acc.inventory.stock_changed")
    assert changed and changed[-1]["changes"] == [{
        "item_id": str(item.id), "location_id": str(main.id), "qty_delta": "10.0000",
    }]

    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(3)}])
    low = fresh("acc.inventory.low_stock")
    assert low and low[-1]["sku"] == item.sku
    assert low[-1]["qty"] == "3.0000"
    assert low[-1]["threshold"] == "3.0000"


def test_item_rules_create_update_delete(db):
    user_id = _admin_id(db)
    item = _make_item(db)

    # дубль sku — 422
    with pytest.raises(AccountingError, match="sku_already_exists"):
        service.create_item(db, company_id=_co(db), data={"sku": item.sku, "name": "dup", "kind": "physical",
                                 "unit_code": "шт"})
        db.commit()

    # активную с движениями нельзя ни деактивировать-удалить, ни менять kind
    main = _location(db, "Основной склад")
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(1),
                                 "unit_cost": Decimal(1)}])
    with pytest.raises(AccountingError, match="item_has_moves"):
        item.is_active = False
        db.commit()
        service.delete_item(db, item)
    with pytest.raises(AccountingError, match="kind is immutable"):
        service.update_item(db, item, {"kind": "digital"})
        db.commit()

    # без движений: неактивную можно удалить, активную нельзя
    clean = _make_item(db)
    with pytest.raises(AccountingError, match="Only inactive"):
        service.delete_item(db, clean)
        db.commit()
    service.update_item(db, clean, {"is_active": False})
    db.commit()
    service.delete_item(db, clean)
    db.commit()
    assert db.get(m.Item, clean.id) is None


def test_transit_location_guard(db):
    user_id = _admin_id(db)
    item = _make_item(db)
    transit = _location(db, "Поставщик")
    main = _location(db, "Основной склад")
    with pytest.raises(AccountingError, match="transit"):
        service.transfer_stock(db, user_id=user_id, data={
            "item_id": item.id, "qty": Decimal(1),
            "from_location_id": transit.id, "to_location_id": main.id,
        })
        db.commit()
    with pytest.raises(AccountingError, match="transit"):
        service.adjustment(db, user_id=user_id, data={
            "location_id": transit.id,
            "lines": [{"item_id": item.id, "qty_fact": Decimal(1),
                       "unit_cost": Decimal(1)}],
        })
        db.commit()


def test_no_change_adjustment_is_rejected(db):
    user_id = _admin_id(db)
    item = _make_item(db)
    main = _location(db, "Основной склад")
    _adjust(db, user_id, main, [{"item_id": item.id, "qty_fact": Decimal(2),
                                 "unit_cost": Decimal(10)}])
    with pytest.raises(AccountingError, match="no_change"):
        service.adjustment(db, user_id=user_id, data={
            "location_id": main.id,
            "lines": [{"item_id": item.id, "qty_fact": Decimal(2)}],
        })
        db.commit()
