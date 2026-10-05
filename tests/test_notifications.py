"""Notifications этап A (notifications-spec §13.A): dedup, fan-out,
изоляция, счётчик, read, аудитории, payload-гэп company_id.

Запуск: docker compose exec api pytest tests/test_notifications.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from src.core.auth import hash_password
from src.core.models import Backup, Company, Notification, User
from src.core.notifications.consumers import _make_consumer
from src.core.notifications.service import notify
from src.db import SessionLocal

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=120)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running")
    yield http
    http.close()


@pytest.fixture()
def org():
    """Организация + 2 админа + сотрудник (уникальные имена)."""
    db = SessionLocal()
    company = Company(name=f"NT-A-{RUN}")
    db.add(company)
    db.flush()
    def user(email, role):
        u = User(email=email, password_hash=hash_password("Passw0rd!123"),
                 full_name=email, role=role, company_id=company.id)
        db.add(u)
        return u
    a1 = user(f"nt-a1-{RUN}@t.local", "admin")
    a2 = user(f"nt-a2-{RUN}@t.local", "admin")
    emp = user(f"nt-e-{RUN}@t.local", "user")
    db.commit()
    ids = {"company": company.id, "a1": a1.id, "a2": a2.id, "emp": emp.id,
           "emails": [a1.email, a2.email, emp.email]}
    db.close()
    yield ids
    db = SessionLocal()
    from src.core.models import AuthSession, UserTotp
    db.execute(delete(Notification).where(
        Notification.company_id == ids["company"]))
    db.execute(delete(UserTotp).where(UserTotp.user_id.in_(
        [ids["a1"], ids["a2"], ids["emp"]])))
    db.execute(delete(AuthSession).where(AuthSession.user_id.in_(
        [ids["a1"], ids["a2"], ids["emp"]])))
    db.execute(delete(User).where(User.id.in_(
        [ids["a1"], ids["a2"], ids["emp"]])))
    db.execute(delete(Company).where(Company.id == ids["company"]))
    db.commit()
    db.close()


def _token(client, email):
    login = client.post(f"{API}/auth/login", json={
        "email": email, "password": "Passw0rd!123"}).json()
    assert "access_token" in login, login
    return {"Authorization": f"Bearer {login['access_token']}"}


def _rows(user_id, kind=None):
    db = SessionLocal()
    try:
        q = select(Notification).where(Notification.user_id == user_id)
        if kind:
            q = q.where(Notification.kind == kind)
        return db.scalars(q).all()
    finally:
        db.close()


# ---------- service: fan-out + dedup ----------

def test_fanout_admins_and_dedup(org):
    db = SessionLocal()
    try:
        n1 = notify(db, company_id=org["company"], kind="sync_failed",
                    title="Синхронизация упала: test",
                    body="Ошибка: timeout", entity_id="17",
                    dedup_key=f"sync_failed:17:{RUN}")
        n2 = notify(db, company_id=org["company"], kind="sync_failed",
                    title="Синхронизация упала: test",
                    body="Ошибка: timeout", entity_id="17",
                    dedup_key=f"sync_failed:17:{RUN}")
        db.commit()
        assert (n1, n2) == (2, 0)  # два админа; повтор — ни одной новой
        for uid in (org["a1"], org["a2"]):
            rows = _rows(uid, "sync_failed")
            assert len(rows) == 1
            r = rows[0]
            assert r.severity == "critical" and r.link == "/integrations/sync"
            assert r.audience == "admins" and r.read_at is None
        assert _rows(org["emp"], "sync_failed") == []  # сотрудник — нет
    finally:
        db.close()


def test_platform_admins_audience_company_null():
    db = SessionLocal()
    pla = db.scalars(select(User).where(
        User.is_platform_admin.is_(True), User.is_active.is_(True))).all()
    key = f"backup:{RUN}"
    try:
        created = notify(db, company_id=None, kind="backup_stale",
                         audience="platform_admins", title="Нет свежего бэкапа",
                         dedup_key=key)
        db.commit()
        assert created == len(pla)
        for u in pla:
            rows = _rows(u.id, "backup_stale")
            assert any(r.company_id is None and r.dedup_key == key for r in rows)
    finally:
        db = SessionLocal()
        db.execute(delete(Notification).where(
            Notification.dedup_key == key))
        db.commit()
        db.close()


# ---------- consumer ----------

def test_consumer_sync_failed_event(org):
    consumer = _make_consumer("integration.sync.failed", "sync_failed")
    payload = {"job": "Ozon-заказы", "job_id": "17", "error": "timeout",
               "company_id": str(org["company"])}
    consumer(payload)
    consumer(payload)  # повтор события — без дублей
    for uid in (org["a1"], org["a2"]):
        rows = _rows(uid, "sync_failed")
        assert len(rows) == 1
        assert rows[0].entity_id == "17"
        assert rows[0].title.startswith("Синхронизация упала")


def test_consumer_event_without_company_skipped(org):
    consumer = _make_consumer("integration.sync.failed", "sync_failed")
    before = len(_rows(org["a1"], "sync_failed"))
    consumer({"job": "X", "error": "e"})  # нет company_id → WARNING, 0 строк
    assert len(_rows(org["a1"], "sync_failed")) == before


# ---------- периодика ----------

def test_totp_deadline_reminder(org):
    from src.core.notifications.tasks import totp_deadline_reminder
    db = SessionLocal()
    u = db.get(User, org["a1"])
    u.totp_setup_deadline = datetime.now(UTC) + timedelta(days=3)
    db.commit()
    db.close()
    r1 = totp_deadline_reminder()
    assert r1["created"] >= 1
    rows = _rows(org["a1"], "totp_deadline")
    assert len(rows) == 1 and rows[0].audience == "user"
    assert "3 дня" in rows[0].title
    totp_deadline_reminder()  # тот же день — dedup
    rows = _rows(org["a1"], "totp_deadline")
    assert len(rows) == 1
    # 2FA включена — не напоминаем
    db = SessionLocal()
    from src.core.models import UserTotp
    db.add(UserTotp(user_id=org["a2"], secret_enc="x",
                    enabled_at=datetime.now(UTC)))
    u2 = db.get(User, org["a2"])
    u2.totp_setup_deadline = datetime.now(UTC) + timedelta(days=3)
    db.commit()
    db.close()
    totp_deadline_reminder()
    assert _rows(org["a2"], "totp_deadline") == []
    db = SessionLocal()
    db.execute(delete(UserTotp).where(UserTotp.user_id == org["a2"]))
    for uid in (org["a1"], org["a2"]):
        db.get(User, uid).totp_setup_deadline = None
    db.commit()
    db.close()


def test_backup_stale_check_fresh_backup_present():
    from src.core.notifications.tasks import backup_stale_check
    db = SessionLocal()
    db.add(Backup(file_name=f"nt-{RUN}.dump", size=10, sha256="0" * 64,
                  kind="scheduled", status="verified",
                  created_at=datetime.now(UTC)))
    db.commit()
    try:
        assert backup_stale_check() == {"created": 0}  # свежий есть — тишина
    finally:
        db = SessionLocal()
        db.execute(delete(Backup).where(Backup.file_name == f"nt-{RUN}.dump"))
        db.commit()
        db.close()


# ---------- API ----------

def test_api_list_unread_read_counter(client, org):
    db = SessionLocal()
    notify(db, company_id=org["company"], kind="low_stock",
           title="Низкий остаток: SKU1", body="Остаток 3 при пороге 5",
           entity_id=f"item-{RUN}", dedup_key=f"low_stock:i1:{RUN}")
    notify(db, company_id=org["company"], kind="low_stock",
           title="Низкий остаток: SKU2", entity_id=f"item2-{RUN}",
           dedup_key=f"low_stock:i2:{RUN}")
    notify(db, company_id=org["company"], kind="low_stock",
           title="Низкий остаток: SKU3", entity_id=f"item3-{RUN}",
           dedup_key=f"low_stock:i3:{RUN}")
    db.commit()
    db.close()

    h = _token(client, f"nt-a1-{RUN}@t.local")
    # полный список (совместимость) и пагинированный
    full = client.get(f"{API}/notifications", headers=h).json()
    mine = [n for n in full if RUN in str(n.get("entity_id") or "")]
    assert len(mine) >= 3
    page = client.get(f"{API}/notifications?limit=2&format=paginated", headers=h).json()
    assert len(page["items"]) == 2 and page["total"] == len(full)
    # счётчик сходится со списком
    count = client.get(f"{API}/notifications/unread-count", headers=h).json()["count"]
    unread = client.get(f"{API}/notifications?unread=true&limit=0", headers=h).json()
    assert count == len(unread["items"]) >= 3
    assert unread["total"] == count
    # фильтр по kind
    only = client.get(f"{API}/notifications?kind=sync_failed,backup_stale", headers=h).json()
    assert all(n["kind"] in ("sync_failed", "backup_stale") for n in only)
    # прочитать 2 из 3 своих
    ids = [n["id"] for n in unread["items"][:2]]
    r = client.post(f"{API}/notifications/read", headers=h, json={"ids": ids}).json()
    assert r == {"updated": 2}
    r2 = client.post(f"{API}/notifications/read", headers=h, json={"ids": ids}).json()
    assert r2 == {"updated": 0}  # идемпотентно
    # прочитать все
    r3 = client.post(f"{API}/notifications/read", headers=h, json={"all": True}).json()
    assert r3["updated"] == count - 2
    assert client.get(f"{API}/notifications/unread-count", headers=h).json() == {"count": 0}
    # перечитанные не отдаются фильтром unread
    assert client.get(f"{API}/notifications?unread=true&limit=0",
                      headers=h).json()["items"] == []


def test_api_isolation_and_foreign_ids(client, org):
    db = SessionLocal()
    created = notify(db, company_id=org["company"], kind="sync_failed",
                     title="x", dedup_key=f"sync_failed:iso:{RUN}")
    db.commit()
    db.close()
    assert created == 2
    h_other = _token(client, f"nt-e-{RUN}@t.local")  # сотрудник — не адресат
    lst = client.get(f"{API}/notifications?limit=0", headers=h_other).json()["items"]
    assert all(RUN not in (n.get("entity_id") or "") for n in lst)
    # чужой id — updated: 0, строка адресата не тронута
    rows = _rows(org["a1"], "sync_failed")
    target = next(r for r in rows if r.dedup_key == f"sync_failed:iso:{RUN}")
    r = client.post(f"{API}/notifications/read", headers=h_other,
                    json={"ids": [str(target.id)]}).json()
    assert r == {"updated": 0}
    db = SessionLocal()
    assert db.get(Notification, target.id).read_at is None
    db.close()
    # readonly-сотрудник (роль без прав на модули) — 200
    r2 = client.get(f"{API}/notifications", headers=h_other)
    assert r2.status_code == 200


def test_api_token_forbidden(client, org):
    # без авторизации — 401 (бейдж только людям; X-API-Token см. test_roles)
    r = client.get(f"{API}/notifications")
    assert r.status_code == 401
