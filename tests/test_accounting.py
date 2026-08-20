"""Тесты модуля учёта: расчёты (без БД) и бизнес-правила (требуют PostgreSQL).

DB-тесты используют год 2050, чтобы не пересекаться с данными smoke-прогонов;
номера в последовательностях продолжаются между запусками, поэтому проверяются
формат, уникальность и отсутствие дыр, а не конкретные значения.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.modules.mgmt_accounting import models as m
from src.modules.mgmt_accounting import service


# ---------- Расчёты (без БД) ----------

def test_amount_base_half_up():
    # 100 USD × 90.5555 = 9055.55 — кейс из приёмки
    assert service.compute_base(Decimal("100"), Decimal("90.5555")) == Decimal("9055.55")
    # half-up, а не банковское округление
    assert service.compute_base(Decimal("0.005"), Decimal("1")) == Decimal("0.01")
    assert service.compute_base(Decimal("2.675"), Decimal("1")) == Decimal("2.68")
    assert service.compute_base(Decimal("1000.125"), Decimal("1")) == Decimal("1000.13")


def test_doc_number_format():
    assert service.format_doc_number("ПК", 2026, 1) == "ПК-2026-00001"
    assert service.format_doc_number("СТ", 2026, 123) == "СТ-2026-00123"


# ---------- Бизнес-правила (требуют PostgreSQL) ----------

@pytest.fixture(scope="module")
def db():
    from src.db import SessionLocal, engine

    try:
        engine.connect().close()
    except Exception:
        pytest.skip("PostgreSQL not available")
    yield SessionLocal


def _admin_id(session) -> uuid.UUID:
    from src.core.models import User

    user = session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None, "seed admin not found (python -m src.seed)"
    return user.id


def _account(session, name: str, currency: str) -> m.Account:
    account = session.scalar(select(m.Account).where(m.Account.name == name))
    if account is None:
        account = m.Account(name=name, currency=currency)
        session.add(account)
        session.commit()
    return account


def _income(session, user_id, account_id: uuid.UUID, day: date, amount: str) -> m.Transaction:
    txn = service.create_transaction(session, user_id=user_id, data={
        "kind": "income", "operated_at": day, "amount": Decimal(amount),
        "currency": "RUB", "account_id": account_id,
    })
    return txn


@pytest.mark.integration
def test_doc_numbering_concurrency(db):
    """Параллельные проведения: номера уникальны, без дыр (SELECT ... FOR UPDATE)."""
    setup = db()
    account = _account(setup, "test-нумерация", "RUB")
    user_id = _admin_id(setup)
    setup.close()

    numbers: list[str] = []
    lock = threading.Lock()

    def worker():
        session = db()
        try:
            txn = _income(session, user_id, account.id, date(2050, 1, 15), "10")
            service.post_transaction(session, txn)
            session.commit()
            with lock:
                numbers.append(txn.doc_number)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(numbers) == 4
    assert len(set(numbers)) == 4
    assert all(number.startswith("ПК-2050-") for number in numbers)
    suffixes = sorted(int(number.rsplit("-", 1)[1]) for number in numbers)
    assert suffixes == list(range(suffixes[0], suffixes[0] + 4))  # дыр нет


@pytest.mark.integration
def test_cross_currency_transfer(db):
    """Перевод USD → RUB без явного amount_to: сумма зачисления считается по курсам."""
    session = db()
    try:
        user_id = _admin_id(session)
        rub = _account(session, "test-перевод-RUB", "RUB")
        usd = _account(session, "test-перевод-USD", "USD")
        day = date(2050, 2, 1)
        service.upsert_rate(session, day, "USD", Decimal("90.5555"))
        session.commit()

        txn = service.create_transaction(session, user_id=user_id, data={
            "kind": "transfer", "operated_at": day, "amount": Decimal("100"),
            "currency": "USD", "account_id": usd.id, "account_to_id": rub.id,
        })
        service.post_transaction(session, txn)
        session.commit()

        assert txn.rate == Decimal("90.5555")
        assert txn.amount_base == Decimal("9055.55")
        assert txn.currency_to == "RUB"
        assert txn.rate_to == Decimal("1")
        assert txn.amount_to == Decimal("9055.55")
        assert txn.amount_to_base == Decimal("9055.55")
        assert txn.doc_number.startswith("ПР-2050-")
    finally:
        session.close()


@pytest.mark.integration
def test_storno_cancels_in_report(db):
    """Сторно: инверсия kind, СТ-номер, is_stornoed, отчёт сходится в ноль."""
    session = db()
    try:
        user_id = _admin_id(session)
        account = _account(session, f"test-сторно-{uuid.uuid4().hex[:8]}", "RUB")
        day = date(2050, 4, 10)

        txn = _income(session, user_id, account.id, day, "1000")
        service.post_transaction(session, txn)
        storno = service.create_storno(session, txn, user_id=user_id, reason="ошибка документа")
        session.commit()

        assert txn.is_stornoed is True
        assert storno.kind == "expense"
        assert storno.doc_number.startswith("СТ-2050-")
        assert storno.storno_of_id == txn.id

        # пара гасится в отчёте естественным суммированием
        report = service.cashflow(session, day, day, account.id)
        assert report["opening_balance"] == 0
        assert report["closing_balance"] == 0

        # оба документа остаются в истории
        posted = session.scalars(select(m.Transaction).where(
            m.Transaction.account_id == account.id,
            m.Transaction.status == "posted",
            m.Transaction.operated_at == day,
        )).all()
        assert {t.id for t in posted} == {txn.id, storno.id}
    finally:
        session.close()


@pytest.mark.integration
def test_storno_of_transfer_swaps_sides(db):
    """Сторно перевода: счета и суммы меняются местами, отчёты по обоим счетам в ноль."""
    session = db()
    try:
        user_id = _admin_id(session)
        rub = _account(session, "test-сторно-перевода-RUB", "RUB")
        usd = _account(session, "test-сторно-перевода-USD", "USD")
        day = date(2050, 5, 20)
        service.upsert_rate(session, day, "USD", Decimal("60"))
        session.commit()

        txn = service.create_transaction(session, user_id=user_id, data={
            "kind": "transfer", "operated_at": day, "amount": Decimal("100"),
            "currency": "USD", "account_id": usd.id, "account_to_id": rub.id,
        })
        service.post_transaction(session, txn)
        storno = service.create_storno(session, txn, user_id=user_id, reason="ошибка")
        session.commit()

        assert storno.kind == "transfer"
        assert storno.account_id == rub.id
        assert storno.account_to_id == usd.id
        assert storno.amount == txn.amount_to and storno.amount_to == txn.amount

        for account_id in (rub.id, usd.id):
            report = service.cashflow(session, day, day, account_id)
            assert report["closing_balance"] == 0
    finally:
        session.close()


@pytest.mark.integration
def test_closed_period_blocks_operations(db):
    """Закрытый период запрещает создание; reopen возвращает право проведения."""
    session = db()
    try:
        user_id = _admin_id(session)
        account = _account(session, "test-период", "RUB")
        # повторный прогон: период мог остаться закрытым с прошлого запуска
        if service.get_or_create_period(session, 2050, 3).status == "closed":
            service.reopen_period(session, 2050, 3, user_id=user_id, reason="test reset")
            session.commit()
        service.close_period(session, 2050, 3, user_id=user_id, reason="свод")
        session.commit()

        with pytest.raises(service.AccountingError) as exc:
            _income(session, user_id, account.id, date(2050, 3, 5), "10")
        assert exc.value.status == 422
        session.rollback()

        service.reopen_period(session, 2050, 3, user_id=user_id, reason="правки")
        txn = _income(session, user_id, account.id, date(2050, 3, 5), "10")
        service.post_transaction(session, txn)
        session.commit()
        assert txn.status == "posted"
    finally:
        session.close()


@pytest.mark.integration
def test_patch_posted_writes_version_and_recalc(db):
    """Правка проведённого: версия в record_versions, amount_base пересчитан."""
    from src.core.models import RecordVersion

    session = db()
    try:
        user_id = _admin_id(session)
        account = _account(session, "test-версии", "RUB")
        day = date(2050, 6, 1)

        txn = _income(session, user_id, account.id, day, "100")
        service.post_transaction(session, txn)
        session.commit()
        assert txn.amount_base == Decimal("100.00")

        service.update_transaction(session, txn, user_id=user_id,
                                    changes={"amount": Decimal("200.50")})
        session.commit()
        assert txn.amount_base == Decimal("200.50")

        versions = session.scalars(select(RecordVersion).where(
            RecordVersion.entity_type == "acc.transaction",
            RecordVersion.entity_id == str(txn.id),
        )).all()
        assert len(versions) == 1
        assert versions[0].diff["amount"] == {"old": "100", "new": "200.50"}
    finally:
        session.close()
