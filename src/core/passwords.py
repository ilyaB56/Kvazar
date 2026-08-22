"""Парольная политика (security-p0 п.4): единая проверка для создания
пользователя и смены пароля. Правила: ≥8 символов, минимум одна буква и
одна цифра, запрет топ-500 частых паролей (сравнение в нижнем регистре).
"""

from __future__ import annotations

from pathlib import Path

_BLACKLIST_FILE = Path(__file__).with_name("common_passwords.txt")


def _load_blacklist() -> frozenset[str]:
    try:
        text = _BLACKLIST_FILE.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


BLACKLIST = _load_blacklist()


def validate_password(password: str) -> list[str]:
    """Вернуть список нарушений политики (пустой = пароль приемлем).

    Единая точка правды: используется роутером создания пользователя
    и сменой пароля; ошибки отдаются клиенту как 422 с описанием правил.
    """
    violations: list[str] = []
    if len(password) < 8:
        violations.append("password must be at least 8 characters long")
    if not any(ch.isalpha() for ch in password):
        violations.append("password must contain at least one letter")
    if not any(ch.isdigit() for ch in password):
        violations.append("password must contain at least one digit")
    if password.lower() in BLACKLIST:
        violations.append("password is too common, choose a less predictable one")
    return violations
