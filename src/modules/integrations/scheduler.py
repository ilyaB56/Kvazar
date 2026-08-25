"""Логика cron-планировщика sync jobs (showcase-chain, этап B).

Чистые функции без БД/Celery — тривиально тестируются (ADR-005: расчёты
без состояния — функциями). Порог анти-дубля 55 с: ежеминутный тик beat
успевает увидеть last_run_at предыдущего тика и не встать повторно.
"""

from __future__ import annotations

from datetime import datetime

from croniter import croniter

# защита от постановки дважды в пределах одного beat-тикa (минута)
ANTI_DUPLICATE_SECONDS = 55


def next_run_at(cron: str, start: datetime) -> datetime | None:
    """Следующий момент запуска по cron от start; некорректный cron → None."""
    if not cron:
        return None
    try:
        return croniter(cron, start).get_next(datetime)
    except (ValueError, KeyError):
        return None


def is_due(cron: str, last_run_at: datetime | None, created_at: datetime | None,
           now: datetime) -> bool:
    """Наступило ли время запуска: croniter(cron, last_run or created) <= now.

    Долгий простой не навёрстывается пачкой сам по себе: тик ставит максимум
    один запуск, last_run_at=now переводит расписание в обычный ритм.
    """
    start = last_run_at or created_at or now
    nxt = next_run_at(cron, start)
    return nxt is not None and nxt <= now
