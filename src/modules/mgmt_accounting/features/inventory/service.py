"""Сервисный слой фичи inventory: НСИ, двойная запись движений, себестоимость.

Инварианты (resources-core-spec §2, §4):
- каждое движение — одна строка from → to; остаток = Σ входов − Σ выходов;
  товар возникает/исчезает только в транзитных локациях;
- физический товар — только по physical-локациям, цифровой — по digital
  (нарушение — 422 kind_mismatch); услуга движений не имеет;
- средняя взвешенная avg_cost пересчитывается только приходами;
  расход/перемещение списывают по текущей avg_cost (§4);
- серийный товар: qty = числу кодов, каждый код — Fernet-шифрованный актив;
- количество сверх остатка — 422 insufficient_stock (allow_negative_stock
  из erp_core.settings, по умолчанию false, §11.4).

События (ADR-002: количества/деньги строками): acc.inventory.stock_changed
(батч item×location×qty_delta), acc.inventory.low_stock.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text, func, or_, select
from sqlalchemy.orm import Session

from src.core import crypto, events
from src.core.models import Setting
from src.modules.mgmt_accounting.features.inventory import models as m
from src.modules.mgmt_accounting.service import AccountingError

Q4 = Decimal("0.0001")
NEGATIVE_STOCK_SETTING = "allow_negative_stock"

# Системный транзит для инвентаризации: списание = Склад → Брак,
# излишек = Брак → Склад (§2.2; транзиты — seed миграции 0016)
TRANSIT_ADJUSTMENT = "Брак"

# дельты операции: (item_id, location_id, qty_delta) — собираются локально
# и уходят одним батч-событием stock_changed
Change = tuple[uuid.UUID, uuid.UUID, Decimal]


def quantize4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


# ---------- НСИ ----------

def get_unit(db: Session, code: str) -> m.Unit:
    unit = db.get(m.Unit, code)
    if unit is None or not unit.is_active:
        raise AccountingError(422, f"Unknown or inactive unit: {code}")
    return unit


def create_item(db: Session, data: dict) -> m.Item:
    if db.scalar(select(m.Item).where(m.Item.sku == data["sku"])) is not None:
        raise AccountingError(422, f"sku_already_exists: {data['sku']}")
    get_unit(db, data["unit_code"])
    kind = data["kind"]
    if kind not in m.ITEM_KINDS:
        raise AccountingError(422, f"kind must be one of {m.ITEM_KINDS}")
    tracking = data.get("tracking") or ("serial" if kind == "digital" else "qty")
    if tracking not in m.TRACKING_MODES:
        raise AccountingError(422, f"tracking must be one of {m.TRACKING_MODES}")
    item = m.Item(
        sku=data["sku"],
        name=data["name"],
        kind=kind,
        unit_code=data["unit_code"],
        tracking=tracking,
        barcode=data.get("barcode"),
        sale_price=data.get("sale_price"),
        low_stock_threshold=data.get("low_stock_threshold"),
    )
    db.add(item)
    db.flush()
    return item


def update_item(db: Session, item: m.Item, changes: dict) -> m.Item:
    has_moves = db.scalar(select(m.StockMove.id).where(m.StockMove.item_id == item.id)) is not None
    frozen_when_moves = {"kind", "tracking", "unit_code"}
    for field in changes:
        if has_moves and field in frozen_when_moves:
            raise AccountingError(422, f"{field} is immutable: item has stock moves")
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_at = datetime.now(UTC)
    db.flush()
    return item


def delete_item(db: Session, item: m.Item) -> None:
    """Физическое удаление — только неактивная номенклатура без движений (§6)."""
    if item.is_active:
        raise AccountingError(422, "Only inactive items can be deleted")
    if db.scalar(select(m.StockMove.id).where(m.StockMove.item_id == item.id)) is not None:
        raise AccountingError(422, "item_has_moves: deletion is not allowed")
    db.delete(item)
    db.flush()


def create_location(db: Session, data: dict) -> m.Location:
    kind = data["kind"]
    if kind not in ("physical", "digital"):
        raise AccountingError(422, "kind must be physical or digital")
    name = data["name"].strip()
    if db.scalar(select(m.Location).where(m.Location.name == name)) is not None:
        raise AccountingError(422, f"location_name_exists: {name}")
    # транзитные локации системные (seed) — пользовательские всегда склады
    location = m.Location(name=name, kind=kind, is_transit=False)
    db.add(location)
    db.flush()
    return location


# ---------- Ссылочная целостность и совместимость ----------

def get_item(db: Session, item_id: uuid.UUID) -> m.Item:
    item = db.get(m.Item, item_id)
    if item is None or not item.is_active:
        raise AccountingError(422, f"Unknown or inactive item: {item_id}")
    return item


def get_location(db: Session, location_id: uuid.UUID) -> m.Location:
    location = db.get(m.Location, location_id)
    if location is None or not location.is_active:
        raise AccountingError(422, f"Unknown or inactive location: {location_id}")
    return location


def _check_kind_compat(item: m.Item, *locations: m.Location) -> None:
    """§3.2: физический товар — physical-локации, цифровой — digital (422).

    Транзитные локации (Поставщик/Клиент/Производство/Брак) — вне правила:
    это поток, а не место хранения (П9, гейт 1.2: цифровой код уходит в
    транзит «Брак» командой порчи).
    """
    if item.kind == "service":
        raise AccountingError(422, "service_items_have_no_stock")
    for location in locations:
        if location.is_transit:
            continue
        if location.kind != item.kind:
            raise AccountingError(
                422,
                f"kind_mismatch: {item.kind} item is not allowed "
                f"on {location.kind} location «{location.name}»",
            )


def _transit_by_name(db: Session, name: str) -> m.Location:
    location = db.scalar(
        select(m.Location).where(m.Location.name == name, m.Location.is_transit)
    )
    if location is None:
        raise AccountingError(422, f"System transit location not found: {name}")
    return location


# ---------- Остатки ----------

def location_balance(
    db: Session, item_id: uuid.UUID, location_id: uuid.UUID, on_date: date | None = None
) -> Decimal:
    """Остаток = Σ входов − Σ выходов (двойная запись), опционально на дату."""
    inflow = select(func.coalesce(func.sum(m.StockMove.qty), 0)).where(
        m.StockMove.item_id == item_id, m.StockMove.to_location_id == location_id
    )
    outflow = select(func.coalesce(func.sum(m.StockMove.qty), 0)).where(
        m.StockMove.item_id == item_id, m.StockMove.from_location_id == location_id
    )
    if on_date is not None:
        inflow = inflow.where(m.StockMove.moved_at <= on_date)
        outflow = outflow.where(m.StockMove.moved_at <= on_date)
    return Decimal(db.execute(select(
        inflow.scalar_subquery() - outflow.scalar_subquery()
    )).scalar_one())


def on_hand(db: Session, item_id: uuid.UUID) -> Decimal:
    """Суммарный остаток по всем нетранзитным локациям (порог low_stock — на item)."""
    stock = select(m.Location.id).where(m.Location.is_transit.is_(False))
    inflow = select(func.coalesce(func.sum(m.StockMove.qty), 0)).where(
        m.StockMove.item_id == item_id,
        m.StockMove.to_location_id.in_(stock),
    )
    outflow = select(func.coalesce(func.sum(m.StockMove.qty), 0)).where(
        m.StockMove.item_id == item_id,
        m.StockMove.from_location_id.in_(stock),
    )
    return Decimal(db.execute(select(
        inflow.scalar_subquery() - outflow.scalar_subquery()
    )).scalar_one())


def stock_balances(
    db: Session,
    *,
    on_date: date | None = None,
    location_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Остатки по складам (без транзитных) со стоимостью по avg_cost (§3.6);
    on_date — срез по moved_at, остаток = Σ входов − Σ выходов на дату."""
    query = select(m.StockMove)
    if on_date is not None:
        query = query.where(m.StockMove.moved_at <= on_date)
    balances: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for move in db.scalars(query).all():
        key_in = (move.item_id, move.to_location_id)
        key_out = (move.item_id, move.from_location_id)
        balances[key_in] = balances.get(key_in, Decimal(0)) + move.qty
        balances[key_out] = balances.get(key_out, Decimal(0)) - move.qty

    locations = {
        location.id: location
        for location in db.scalars(select(m.Location)).all()
        if not location.is_transit and (location_id is None or location.id == location_id)
    }
    items = {
        item.id: item
        for item in db.scalars(select(m.Item).where(m.Item.kind != "service")).all()
        if item_id is None or item.id == item_id
    }
    rows: list[dict[str, Any]] = []
    for (item_ref, location_ref), qty in sorted(
        balances.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
    ):
        if qty == 0 or location_ref not in locations or item_ref not in items:
            continue
        item = items[item_ref]
        rows.append({
            "item_id": str(item_ref),
            "sku": item.sku,
            "item_name": item.name,
            "item_kind": item.kind,
            "location_id": str(location_ref),
            "location_name": locations[location_ref].name,
            "location_kind": locations[location_ref].kind,
            "qty": str(quantize4(qty)),
            "avg_cost": str(item.avg_cost) if item.avg_cost is not None else None,
            "value": str(quantize4(qty * item.avg_cost)) if item.avg_cost is not None else None,
        })
    return rows


# ---------- Себестоимость (§4) ----------

def recalc_avg_cost(
    item: m.Item, qty_rest: Decimal, qty_in: Decimal, unit_cost: Decimal
) -> None:
    """Средняя взвешенная: avg = (rest×avg + in×cost)/(rest+in); пересчитывают
    только приходы, атомарно в транзакции. qty_rest — остаток ДО прихода
    (при avg NULL или нулевом остатке avg = цена прихода)."""
    if item.avg_cost is None or qty_rest <= 0:
        item.avg_cost = quantize4(unit_cost)
        return
    item.avg_cost = quantize4(
        (qty_rest * item.avg_cost + qty_in * unit_cost) / (qty_rest + qty_in)
    )


def allow_negative_stock(db: Session) -> bool:
    row = db.scalar(select(Setting).where(Setting.key == NEGATIVE_STOCK_SETTING))
    if row is None:
        return False
    # значение строковое: 'true'/'1' — включено, остальное (в т.ч. 'false') — нет
    return str(row.value).strip().lower() in ("true", "1", "yes")


def void_serial(db: Session, *, code: str, user_id: uuid.UUID, note: str = "") -> m.StockMove:
    """Д13: испортить код цифрового товара — движение «локация → Брак»
    (транзит) + status=void; код больше не выдаётся и не возвращается."""
    serial = db.scalar(
        select(m.ItemSerial).where(
            m.ItemSerial.code_hash == _serial_hash(code.strip()),
            m.ItemSerial.status == "in_stock",
        )
    )
    if serial is None:
        raise AccountingError(404, f"serial_not_found_or_not_in_stock")
    item = db.get(m.Item, serial.item_id)
    location = db.get(m.Location, serial.location_id)
    scrap = db.scalar(select(m.Location).where(
        m.Location.name == "Брак", m.Location.is_transit))
    if item is None or location is None or scrap is None:
        raise AccountingError(422, "item_or_location_not_found")
    lock_stock(db, (item.id, location.id))
    move = _apply_issue(
        db, item=item, from_location=location, qty=Decimal(1),
        user_id=user_id, to_location=scrap, counterparty_id=None,
        source_type="serial_void", source_id=serial.id, moved_at=date.today(),
        note=note or f"Порча кода {code.strip()[:4]}…",
        serial_codes=[code.strip()], void_serials=True,
    )
    return move


def lock_stock(db: Session, *pairs: tuple[uuid.UUID, uuid.UUID]) -> None:
    """Д6: сериализация проведения документов по парам item×location.

    Остатки — агрегат по движениям (нет строки баланса для FOR UPDATE),
    поэтому транзакционный advisory-lock: конкурентные post отгрузок/
    приёмок по одному item×location выстраиваются в очередь, второй
    видит уже списанный остаток и получает insufficient_stock.
    """
    for item_id, location_id in pairs:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                   {"k": f"stock:{item_id}:{location_id}"})


def _check_stock_enough(db: Session, item: m.Item, location: m.Location, qty: Decimal) -> None:
    if allow_negative_stock(db):
        return
    if location_balance(db, item.id, location.id) < qty:
        raise AccountingError(422, f"insufficient_stock: {item.sku} on «{location.name}»")


# ---------- Серийники ----------

def _serial_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def serial_cost(db: Session, serial: m.ItemSerial) -> Decimal | None:
    """Себестоимость конкретного кода: unit_cost движения его прихода."""
    if serial.received_move_id is None:
        return None
    move = db.get(m.StockMove, serial.received_move_id)
    return move.unit_cost if move else None


def serial_code(row: m.ItemSerial) -> str:
    """Расшифровать код: серийник — актив, хранится Fernet-шифрованным (§11.1)."""
    return crypto.decrypt_str(row.code_enc)


def _register_serial(
    db: Session, *, item: m.Item, code: str, location: m.Location,
    unit_cost: Decimal | None, move_id: uuid.UUID, moved_at: date,
) -> m.ItemSerial:
    code = code.strip()
    if not code:
        raise AccountingError(422, "serial code must not be empty")
    if db.scalar(select(m.ItemSerial).where(m.ItemSerial.code_hash == _serial_hash(code))):
        raise AccountingError(422, f"serial_already_exists: {code}")
    serial = m.ItemSerial(
        item_id=item.id,
        code_enc=crypto.encrypt_str(code),
        code_hash=_serial_hash(code),
        status="in_stock",
        location_id=location.id,
        unit_cost=unit_cost,
        received_move_id=move_id,
        received_at=moved_at,
    )
    db.add(serial)
    db.flush()  # сессия без autoflush: сразу виден последующим проверкам
    return serial


def _find_serial(
    db: Session, *, item: m.Item, code: str, location: m.Location | None = None
) -> m.ItemSerial:
    serial = db.scalar(
        select(m.ItemSerial).where(
            m.ItemSerial.item_id == item.id,
            m.ItemSerial.code_hash == _serial_hash(code.strip()),
            m.ItemSerial.status == "in_stock",
        )
    )
    if serial is None:
        raise AccountingError(422, f"serial_not_found: {code}")
    if location is not None and serial.location_id != location.id:
        raise AccountingError(
            422, f"serial_not_on_location: {code} is not on «{location.name}»"
        )
    return serial


def _check_serial_qty(item: m.Item, qty: Decimal, codes: list[str]) -> None:
    if qty != len(codes):
        raise AccountingError(
            422, f"serial_qty_mismatch: qty={qty} but {len(codes)} codes given for {item.sku}"
        )
    if qty != qty.to_integral_value():
        raise AccountingError(422, "serial item qty must be a whole number")


# ---------- Движения ----------

def _validate_qty(qty: Decimal) -> Decimal:
    if qty is None or qty <= 0:
        raise AccountingError(422, "qty must be positive")
    return quantize4(qty)


def _publish_stock_changed(db: Session, changes: list[Change], source_type: str) -> None:
    events.publish(db, "acc.inventory.stock_changed", {
        "source_type": source_type,
        "changes": [
            {
                "item_id": str(item_id),
                "location_id": str(location_id),
                "qty_delta": str(quantize4(delta)),
            }
            for item_id, location_id, delta in changes
        ],
    })


def _check_low_stock(db: Session, item: m.Item) -> None:
    if item.low_stock_threshold is None:
        return
    qty = on_hand(db, item.id)
    if qty <= item.low_stock_threshold:
        events.publish(db, "acc.inventory.low_stock", {
            "item_id": str(item.id),
            "sku": item.sku,
            "qty": str(quantize4(qty)),
            "threshold": str(quantize4(item.low_stock_threshold)),
        })


def transfer_stock(db: Session, *, user_id: uuid.UUID, data: dict) -> m.StockMove:
    """Перемещение между складами одного вида; себестоимость не меняется."""
    item = get_item(db, data["item_id"])
    from_location = get_location(db, data["from_location_id"])
    to_location = get_location(db, data["to_location_id"])
    if from_location.id == to_location.id:
        raise AccountingError(422, "transfer_to_same_location")
    if from_location.is_transit or to_location.is_transit:
        raise AccountingError(422, "transfer_to_or_from_transit_is_not_allowed")
    _check_kind_compat(item, from_location, to_location)
    qty = _validate_qty(data["qty"])
    codes = data.get("serial_codes") or []
    serials: list[m.ItemSerial] = []
    if item.tracking == "serial":
        _check_serial_qty(item, qty, codes)
        # все проверки — до вставки движения: отказ не оставляет грязной сессии
        serials = [_find_serial(db, item=item, code=code, location=from_location)
                   for code in codes]
    _check_stock_enough(db, item, from_location, qty)

    move = m.StockMove(
        item_id=item.id,
        qty=qty,
        unit_cost=item.avg_cost,
        from_location_id=from_location.id,
        to_location_id=to_location.id,
        counterparty_id=data.get("counterparty_id"),
        source_type="stock_transfer",
        moved_at=data.get("moved_at") or date.today(),
        created_by=user_id,
        note=data.get("note", ""),
    )
    db.add(move)
    db.flush()
    for serial in serials:
        serial.location_id = to_location.id
    _publish_stock_changed(db, [
        (item.id, from_location.id, -qty),
        (item.id, to_location.id, qty),
    ], "stock_transfer")
    _check_low_stock(db, item)
    return move


def _apply_receipt(
    db: Session, *, item: m.Item, to_location: m.Location, qty: Decimal,
    unit_cost: Decimal | None, user_id: uuid.UUID, from_location: m.Location,
    counterparty_id: uuid.UUID | None, source_type: str, source_id: uuid.UUID | None,
    moved_at: date, note: str, serial_codes: list[str] | None = None,
) -> m.StockMove:
    """Приход на склад из транзита: движение + пересчёт avg_cost + серийники."""
    qty_rest = on_hand(db, item.id)  # остаток ДО прихода (для средней)
    if item.tracking == "serial":
        # предпроверка кодов до вставки движения: отказ не оставляет грязь
        for code in serial_codes or []:
            if not code.strip():
                raise AccountingError(422, "serial code must not be empty")
            if db.scalar(select(m.ItemSerial).where(
                m.ItemSerial.code_hash == _serial_hash(code)
            )):
                raise AccountingError(422, f"serial_already_exists: {code}")
    move = m.StockMove(
        item_id=item.id,
        qty=qty,
        unit_cost=unit_cost,
        from_location_id=from_location.id,
        to_location_id=to_location.id,
        counterparty_id=counterparty_id,
        source_type=source_type,
        source_id=source_id,
        moved_at=moved_at,
        created_by=user_id,
        note=note,
    )
    db.add(move)
    db.flush()
    if item.tracking == "serial":
        for code in serial_codes or []:
            _register_serial(
                db, item=item, code=code, location=to_location,
                unit_cost=unit_cost, move_id=move.id, moved_at=moved_at,
            )
    if unit_cost is not None:
        recalc_avg_cost(item, qty_rest, qty, unit_cost)
    _publish_stock_changed(db, [(item.id, to_location.id, qty)], source_type)
    return move


def _apply_issue(
    db: Session, *, item: m.Item, from_location: m.Location, qty: Decimal,
    user_id: uuid.UUID, to_location: m.Location, counterparty_id: uuid.UUID | None,
    source_type: str, source_id: uuid.UUID | None, moved_at: date, note: str,
    serial_codes: list[str] | None = None, void_serials: bool = False,
) -> m.StockMove:
    """Расход со склада в транзит: контроль остатка, списание по avg_cost (§4)."""
    _check_stock_enough(db, item, from_location, qty)
    serials: list[m.ItemSerial] = []
    issue_cost = item.avg_cost
    if item.tracking == "serial":
        serials = [_find_serial(db, item=item, code=code, location=from_location)
                   for code in serial_codes or []]
        # П10 (решение основателя): себестоимость выдачи цифровых — по
        # unit_cost прихода конкретных выданных кодов, не средняя по товару
        costs = [serial_cost(db, serial) for serial in serials]
        costs = [c for c in costs if c is not None]
        if costs:
            issue_cost = (sum(costs) / Decimal(len(costs))).quantize(Decimal("1e-4"))
    move = m.StockMove(
        item_id=item.id,
        qty=qty,
        unit_cost=issue_cost,
        from_location_id=from_location.id,
        to_location_id=to_location.id,
        counterparty_id=counterparty_id,
        source_type=source_type,
        source_id=source_id,
        moved_at=moved_at,
        created_by=user_id,
        note=note,
    )
    db.add(move)
    db.flush()
    for serial in serials:
        if void_serials:
            serial.status = "void"
            serial.location_id = None
        else:
            serial.status = "sold"
            serial.sold_move_id = move.id
            serial.location_id = None
    _publish_stock_changed(db, [(item.id, from_location.id, -qty)], source_type)
    return move


def adjustment(db: Session, *, user_id: uuid.UUID, data: dict) -> list[m.StockMove]:
    """Инвентаризация локации: строки «факт»; излишек — приход из «Брака»
    по avg_cost (первый приход — по переданной цене), недостача — списание
    в «Брак» по avg_cost (§4). Серийный товар: факт = список кодов на локации.
    """
    location = get_location(db, data["location_id"])
    if location.is_transit:
        raise AccountingError(422, "adjustment_of_transit_location_is_not_allowed")
    transit = _transit_by_name(db, TRANSIT_ADJUSTMENT)
    moved_at = data.get("moved_at") or date.today()
    note = data.get("note", "")
    moves: list[m.StockMove] = []
    for line in data["lines"]:
        item = get_item(db, line["item_id"])
        _check_kind_compat(item, location)
        if item.tracking == "serial":
            move = _adjust_serial_line(
                db, item=item, location=location, transit=transit,
                codes=[c.strip() for c in line.get("serial_codes") or []],
                unit_cost=line.get("unit_cost"), user_id=user_id,
                moved_at=moved_at, note=note,
            )
        else:
            move = _adjust_qty_line(
                db, item=item, location=location, transit=transit,
                qty_fact=_validate_qty(line["qty_fact"]),
                unit_cost=line.get("unit_cost"), user_id=user_id,
                moved_at=moved_at, note=note,
            )
        moves.append(move)
        _check_low_stock(db, item)
    return moves


def _surplus_cost(item: m.Item, unit_cost: Decimal | None) -> Decimal:
    """Излишек по avg_cost; явная цена — только когда avg ещё нет (§4)."""
    if item.avg_cost is not None:
        return item.avg_cost
    if unit_cost is not None:
        return unit_cost
    raise AccountingError(
        422, f"unit_cost_required: first receipt of {item.sku} needs a cost"
    )


def _adjust_qty_line(
    db: Session, *, item: m.Item, location: m.Location, transit: m.Location,
    qty_fact: Decimal, unit_cost: Decimal | None, user_id: uuid.UUID,
    moved_at: date, note: str,
) -> m.StockMove:
    balance = location_balance(db, item.id, location.id)
    delta = qty_fact - balance
    if delta > 0:
        return _apply_receipt(
            db, item=item, to_location=location, qty=delta,
            unit_cost=_surplus_cost(item, unit_cost), user_id=user_id,
            from_location=transit, counterparty_id=None,
            source_type="stock_adjustment", source_id=None, moved_at=moved_at,
            note=note or "inventory surplus", serial_codes=None,
        )
    if delta < 0:
        return _apply_issue(
            db, item=item, from_location=location, qty=-delta, user_id=user_id,
            to_location=transit, counterparty_id=None,
            source_type="stock_adjustment", source_id=None, moved_at=moved_at,
            note=note or "inventory shortage", serial_codes=None, void_serials=True,
        )
    raise AccountingError(422, f"no_change: {item.sku} already {qty_fact} on «{location.name}»")


def _adjust_serial_line(
    db: Session, *, item: m.Item, location: m.Location, transit: m.Location,
    codes: list[str], unit_cost: Decimal | None, user_id: uuid.UUID,
    moved_at: date, note: str,
) -> m.StockMove:
    present = db.scalars(
        select(m.ItemSerial).where(
            m.ItemSerial.item_id == item.id,
            m.ItemSerial.status == "in_stock",
            m.ItemSerial.location_id == location.id,
        )
    ).all()
    present_hashes = {row.code_hash for row in present}
    fact_hashes = {_serial_hash(code) for code in codes}
    if len(fact_hashes) != len(codes):
        raise AccountingError(422, "duplicate serial codes in adjustment")
    missing = [row for row in present if row.code_hash not in fact_hashes]
    new_codes = [code for code in codes if _serial_hash(code) not in present_hashes]

    move: m.StockMove | None = None
    if new_codes:
        move = _apply_receipt(
            db, item=item, to_location=location, qty=Decimal(len(new_codes)),
            unit_cost=_surplus_cost(item, unit_cost), user_id=user_id,
            from_location=transit, counterparty_id=None,
            source_type="stock_adjustment", source_id=None, moved_at=moved_at,
            note=note or "inventory surplus (serials)", serial_codes=new_codes,
        )
    if missing:
        issue = _apply_issue(
            db, item=item, from_location=location, qty=Decimal(len(missing)),
            user_id=user_id, to_location=transit, counterparty_id=None,
            source_type="stock_adjustment", source_id=None, moved_at=moved_at,
            note=note or "inventory shortage (serials)",
            serial_codes=[serial_code(row) for row in missing], void_serials=True,
        )
        move = move or issue
    if move is None:
        raise AccountingError(
            422, f"no_change: serials of {item.sku} already match «{location.name}»"
        )
    return move


# ---------- Журнал движений ----------

def stock_moves(
    db: Session,
    *,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[m.StockMove]:
    query = select(m.StockMove).order_by(m.StockMove.moved_at, m.StockMove.created_at)
    if item_id is not None:
        query = query.where(m.StockMove.item_id == item_id)
    if location_id is not None:
        query = query.where(or_(
            m.StockMove.from_location_id == location_id,
            m.StockMove.to_location_id == location_id,
        ))
    if date_from is not None:
        query = query.where(m.StockMove.moved_at >= date_from)
    if date_to is not None:
        query = query.where(m.StockMove.moved_at <= date_to)
    return db.scalars(query).all()


def move_payload(move: m.StockMove) -> dict[str, Any]:
    """Ответ API: количества/деньги строками (ADR-003)."""
    return {
        "id": str(move.id),
        "item_id": str(move.item_id),
        "qty": str(move.qty),
        "unit_cost": str(move.unit_cost) if move.unit_cost is not None else None,
        "from_location_id": str(move.from_location_id),
        "to_location_id": str(move.to_location_id),
        "counterparty_id": str(move.counterparty_id) if move.counterparty_id else None,
        "source_type": move.source_type,
        "moved_at": move.moved_at.isoformat(),
        "note": move.note,
    }
