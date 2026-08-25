"""Витринная цепочка (showcase-chain-spec): cron-планировщик и парсер ЦБ РФ."""

from __future__ import annotations

from datetime import datetime

from src.modules.integrations import scheduler


def test_every_minute_job_due():
    last = datetime(2026, 8, 25, 12, 0, 0)
    assert not scheduler.is_due("* * * * *", last, last, datetime(2026, 8, 25, 12, 0, 59))
    assert scheduler.is_due("* * * * *", last, last, datetime(2026, 8, 25, 12, 1, 0))
    # и в любую следующую минуту
    assert scheduler.is_due("* * * * *", last, last, datetime(2026, 8, 25, 12, 5, 30))


def test_daily_job_not_due_outside_window():
    created = datetime(2026, 8, 24, 0, 0, 0)
    # первый запуск (last_run нет): наступает в первый намеченный момент
    assert not scheduler.is_due("0 3 * * *", None, created, datetime(2026, 8, 24, 2, 59, 0))
    assert scheduler.is_due("0 3 * * *", None, created, datetime(2026, 8, 24, 3, 0, 0))
    # после запуска в 03:00 — не due до следующего дня
    last = datetime(2026, 8, 24, 3, 0, 0)
    assert not scheduler.is_due("0 3 * * *", last, created, datetime(2026, 8, 25, 2, 59, 0))
    assert scheduler.is_due("0 3 * * *", last, created, datetime(2026, 8, 25, 3, 0, 0))


def test_no_catchup_burst_after_long_downtime():
    # коробка стояла неделю: намеченный момент давно прошёл — due ровно один раз,
    # после запуска (last_run=now) расписание продолжается в обычном ритме
    last = datetime(2026, 8, 18, 3, 0, 0)
    assert scheduler.is_due("0 3 * * *", last, last, datetime(2026, 8, 25, 12, 0, 0))
    resumed = datetime(2026, 8, 25, 12, 0, 0)
    assert not scheduler.is_due("0 3 * * *", resumed, last, datetime(2026, 8, 25, 12, 1, 0))
    assert scheduler.is_due("0 3 * * *", resumed, last, datetime(2026, 8, 26, 3, 0, 30))


def test_invalid_or_empty_cron_not_due():
    now = datetime(2026, 8, 25, 12, 0, 0)
    assert scheduler.next_run_at("", now) is None
    assert scheduler.next_run_at("not a cron", now) is None
    assert not scheduler.is_due("not a cron", None, now, now)


def test_anti_duplicate_threshold():
    # окно анти-дубля: повтор в пределах 55 с от last_run не считается due
    # (проверяем сам порог, использованный в SQL-условии планировщика)
    assert scheduler.ANTI_DUPLICATE_SECONDS == 55
