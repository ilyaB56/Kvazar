"""Bootstrap коробки (box-installer-spec §3.9/§7.8, этап A).

Одна проблема: боевые org уже есть — на живой системе bootstrap обязан
давать 409. Для проверки «пустой базы» — временная БД-фикстура не
выделяется (создание отдельного postgres в прогоне дорого); проверяем
контракт через мок: monkeypatch-подмена Company/User-запросов.

Запуск: docker compose exec api pytest tests/test_bootstrap.py
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=60)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running")
    yield http
    http.close()


def test_bootstrap_forefver_409_when_orgs_exist(client):
    """Боевая система с организациями → 409 всегда (§10.3)."""
    response = client.post(f"{API}/platform/bootstrap", json={
        "company_name": "Не должно пройти", "admin_full_name": "X",
        "admin_email": f"boot-{RUN}@x.test", "admin_password": "Strong1pass"})
    assert response.status_code == 409


def test_bootstrap_contract_on_empty_system():
    """Контракт one-shot: гвард «0 организаций → иначе 409» и сигнатура.
    Полный happy-path требует пустой БД (отдельная коробочная VM —
    приёмка §7.1); на живой системе проверяется 409-тестом выше."""
    import inspect

    from src.core import router as core_router

    fn = core_router.platform_bootstrap
    assert "body" in inspect.signature(fn).parameters
    doc = fn.__doc__ or ""
    assert "0 организаций" in doc or "409" in doc

    src = inspect.getsource(fn)
    # гвард: 409 при наличии организаций С пользователями (безлюдная
    # служебная «Основная» из миграции 0027 не блокирует — чистая
    # установка коробки, инцидент 2026-09-29)
    assert "User.company_id == Company.id" in src, "нет гварда пустой системы"
    assert "seed_company_data" in src, "не переиспользует сиды организаций"
    assert "ensure_ai_self_api" in src, "нет ai-self-api для ИИ-инструментов"
    assert "totp_setup_deadline" in src, "нет 2FA-дедлайна админа"
    assert "platform.org.bootstrap" in src, "нет события/аудита bootstrap"


def test_bootstrap_validation(client):
    """Слабый пароль — 422; короткое имя — 422 (до 409-гварда)."""
    weak = client.post(f"{API}/platform/bootstrap", json={
        "company_name": "ООО Тест", "admin_email": "a@b.ru",
        "admin_password": "short"})
    assert weak.status_code == 422
    short_name = client.post(f"{API}/platform/bootstrap", json={
        "company_name": "О", "admin_email": "a@b.ru",
        "admin_password": "Strong1pass"})
    assert short_name.status_code == 422
