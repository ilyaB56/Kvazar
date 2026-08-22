"""Unit-тесты парольной политики и лимитера логина (без БД и сети)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core import rate_limit
from src.core.passwords import validate_password

# ---------- Парольная политика (security-p0 п.4) ----------


@pytest.mark.parametrize("password", [
    "12345678",      # топ-500 + нет буквы
    "password1",     # топ-500
    "qwerty123",     # топ-500
    "monkey123",     # топ-500
    "sh0rt",         # < 8 символов
    "abcdefgh",      # нет цифры
    "1234567890a",   # ок — контроль, что не баним всё подряд
])
def test_policy(password):
    weak = password in {"12345678", "password1", "qwerty123", "monkey123", "sh0rt", "abcdefgh"}
    assert (validate_password(password) != []) is weak


def test_strong_passwords_pass():
    for password in ("admin12345", "Str0ngPass9", "korobka4Life", "P@ssw0rd!2026"):
        assert validate_password(password) == [], password


def test_blacklist_compare_lowercase():
    # сравнение в нижнем регистре: PASSWORD1 баним так же, как password1
    assert validate_password("PASSWORD1") != []
    assert validate_password("Admin12345") == []


# ---------- Лимитер логина (security-p0 п.2) ----------


class FakeRedis:
    def __init__(self):
        self.data: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    def expire(self, key: str, ttl: int) -> None:
        pass

    def ttl(self, key: str) -> int:
        return 42

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


class FakeRequest:
    def __init__(self):
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="10.0.0.1")


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", fake)
    monkeypatch.setattr(rate_limit, "_warned", True)
    return fake


def test_rate_limit_blocks_sixth_attempt(fake_redis):
    request = FakeRequest()
    for _ in range(rate_limit.MAX_ATTEMPTS):
        rate_limit.check_login_rate_limit(request)
    with pytest.raises(HTTPException) as exc:
        rate_limit.check_login_rate_limit(request)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) > 0


def test_rate_limit_success_resets(fake_redis):
    request = FakeRequest()
    for _ in range(4):
        rate_limit.check_login_rate_limit(request)
    rate_limit.reset_login_rate_limit(request)
    for _ in range(rate_limit.MAX_ATTEMPTS):
        rate_limit.check_login_rate_limit(request)


def test_rate_limit_fail_open(monkeypatch):
    class BrokenRedis:
        def incr(self, key):
            raise ConnectionError("redis down")

        def expire(self, key, ttl):
            raise ConnectionError("redis down")

        def delete(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "_redis", BrokenRedis())
    monkeypatch.setattr(rate_limit, "_warned", True)
    # Redis недоступен — логин не блокируется (оффлайн-коробка важнее)
    rate_limit.check_login_rate_limit(FakeRequest())
    rate_limit.reset_login_rate_limit(FakeRequest())
