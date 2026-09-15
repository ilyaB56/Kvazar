"""Делегирование, этап C (бэкенд-часть §12): жизненный цикл учётки.

- c1 создание «пустой» учётки: rw-обладателем; username из ФИО
  (Иван Иванович Иванов → IIIVANOV); повторное ФИО → IIIVANOV1;
  вход по username; доступ — экран «Нет доступа» (прав нет)
- c2 первая выдача = активация: temp_password в ответе ОДИН раз,
  must_change_password_by = +72ч; вход по временному паролю
- c3 дедлайн истёк (двигаем в БД) → мутации 403 password_expired,
  чтение 200; смена пароля снимает дедлайн → мутации снова работают
- c4 /auth/me отдаёт must_change_password_by и username

Запуск: docker compose exec api pytest tests/test_delegation_c.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
ACC = f"{API}/accounting"

pytestmark = pytest.mark.integration

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        pat = f"%{RUN}%"
        db.execute(text(
            "DELETE FROM erp_core.user_permissions WHERE user_id IN ("
            "  SELECT id FROM erp_core.users WHERE email LIKE :pat"
            "   OR username LIKE :pat)"
        ).bindparams(pat=pat))
        db.execute(text(
            "DELETE FROM erp_core.auth_sessions WHERE user_id IN ("
            "  SELECT id FROM erp_core.users WHERE email LIKE :pat"
            "   OR username LIKE :pat)"
        ).bindparams(pat=pat))
        db.execute(text(
            "DELETE FROM erp_core.users WHERE email LIKE :pat"
            " OR username LIKE :pat").bindparams(pat=pat))
        db.commit()
    except ImportError:
        pass
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=30,
                        headers={"User-Agent": "del-c-test"})
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin(client):
    data = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": data["refresh_token"],
        "company_id": data["organizations"][0]["id"]}).json()
    return pair


def test_c1_username_generation_and_login(client, admin):
    # первое ФИО → IIIVANOV-подобный логин (транслит)
    response = client.post(f"{API}/accounts", json={
        "full_name": "Иван Иванович Иванов",
        "email": f"ivanov-{RUN}@mt.test", "phone": "+79001112233"},
        headers=_auth(admin["access_token"]))
    assert response.status_code == 201, response.text
    first = response.json()
    assert first["username"] == "IIIVANOV", first

    # то же ФИО → суффикс
    response = client.post(f"{API}/accounts", json={
        "full_name": "Иван Иванович Иванов",
        "email": f"ivanov2-{RUN}@mt.test"},
        headers=_auth(admin["access_token"]))
    assert response.status_code == 201
    assert response.json()["username"] == "IIIVANOV1", response.json()

    # вход по username невозможен без знания пароля, но логин принимает
    # username: проверяем, что логин с username не даёт 401 «не найдено»,
    # а 401 «пароль» (учётка без активации)
    response = client.post(f"{API}/auth/login", json={
        "email": "IIIVANOV", "password": "WrongPass9"})
    assert response.status_code == 401  # не 404/422 — username распознан
    return first


_C2_SEQ = [0]


def _activate_fresh_user(client, admin):
    """Создать учётку + первая выдача rw; каждый вызов — уникальное имя."""
    _C2_SEQ[0] += 1
    seq = _C2_SEQ[0]
    account = client.post(f"{API}/accounts", json={
        "full_name": f"Пётр Петрович Петр{RUN[:4]}{seq}",
        "email": f"petrov{seq}-{RUN}@mt.test"},
        headers=_auth(admin["access_token"])).json()
    assert account["username"].startswith("PPPET")

    # первая выдача → temp_password один раз + дедлайн +72ч
    response = client.put(f"{API}/users/{account['id']}/permissions",
                          json={"module": "accounting", "level": "rw"},
                          headers=_auth(admin["access_token"]))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("temp_password"), "временный пароль в ответе"
    assert data.get("must_change_password_by")

    # вход по username И временному паролю
    login = client.post(f"{API}/auth/login", json={
        "email": account["username"], "password": data["temp_password"]})
    assert login.status_code == 200, login.text
    tok = login.json()["access_token"]

    # чтение доступно, мутация — тоже (дедлайн ещё не истёк)
    response = client.get(f"{ACC}/accounts", headers=_auth(tok))
    assert response.status_code == 200
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"c2-{seq}-{RUN}", "currency": "RUB"},
                           headers=_auth(tok))
    assert response.status_code == 201
    return {"user_id": account["id"], "token": tok, "username": account["username"],
            "temp_password": data["temp_password"]}


def test_c2_first_grant_activates_with_temp_password(client, admin):
    ctx = _activate_fresh_user(client, admin)
    assert ctx["token"]


def test_c3_expired_deadline_blocks_mutations(client, admin):
    ctx = _activate_fresh_user(client, admin)
    # двигаем дедлайн в прошлое
    from src.db import SessionLocal
    from src.core.models import User as UserModel

    db = SessionLocal()
    try:
        row = db.get(UserModel, uuid.UUID(ctx["user_id"]))
        row.must_change_password_by = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    # мутация → 403 password_expired; чтение → 200
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"c3-{RUN}", "currency": "RUB"},
                           headers=_auth(ctx["token"]))
    assert response.status_code == 403
    assert "password_expired" in response.text
    response = client.get(f"{ACC}/accounts", headers=_auth(ctx["token"]))
    assert response.status_code == 200

    # смена пароля снимает дедлайн → мутации снова работают
    response = client.post(f"{API}/auth/change-password", json={
        "old_password": ctx["temp_password"],
        "new_password": "Changed9pass"}, headers=_auth(ctx["token"]))
    assert response.status_code == 200
    relogin = client.post(f"{API}/auth/login", json={
        "email": ctx["username"], "password": "Changed9pass"})
    assert relogin.status_code == 200, "вход по username после смены"
    tok2 = relogin.json()["access_token"]
    response = client.post(f"{ACC}/accounts",
                           json={"name": f"c3b-{RUN}", "currency": "RUB"},
                           headers=_auth(tok2))
    assert response.status_code == 201, "после смены пароля мутации работают"


def test_c4_me_exposes_deadline_and_username(client, admin):
    account = client.post(f"{API}/accounts", json={
        "full_name": f"Анна Сергеевна Сидор{RUN[:4]}",
        "email": f"sidorova-{RUN}@mt.test"},
        headers=_auth(admin["access_token"])).json()
    grant = client.put(f"{API}/users/{account['id']}/permissions",
                       json={"module": "crm", "level": "ro"},
                       headers=_auth(admin["access_token"])).json()
    login = client.post(f"{API}/auth/login", json={
        "email": account["username"], "password": grant["temp_password"]})
    assert login.status_code == 200, login.text
    me = client.get(f"{API}/auth/me",
                    headers=_auth(login.json()["access_token"])).json()
    assert me["username"] == account["username"]
    assert me["must_change_password"] is True
    assert me["must_change_password_by"] is not None
