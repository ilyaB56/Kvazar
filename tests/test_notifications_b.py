"""Notifications этапы B+C (notifications-spec §13.B/§13.C): события →
уведомления (online-заказы, signup, ai-предложения, CRM-просрочки),
каналы правил, мьюты типов, company-фильтр telegram-connection,
ретеншн.

Запуск: docker compose exec api pytest tests/test_notifications_b.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import delete, select

from src.core.auth import hash_password
from src.core.models import Company, Notification, Setting, User
from src.core.notifications.consumers import _make_consumer, in_app_allowed
from src.core.notifications.service import notify
from src.db import SessionLocal
from src.modules.integrations import models as im
from src.modules.integrations.notify import pick_telegram_connection
from src.modules.mini_crm import models as crm

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
    company = Company(name=f"NT-BC-{RUN}")
    db.add(company)
    db.flush()
    def user(email, role):
        u = User(email=email, password_hash=hash_password("Passw0rd!123"),
                 full_name=email, role=role, company_id=company.id)
        db.add(u)
        return u
    a1 = user(f"nt-bc1-{RUN}@t.local", "admin")
    a2 = user(f"nt-bc2-{RUN}@t.local", "admin")
    emp = user(f"nt-bce-{RUN}@t.local", "user")
    db.commit()
    ids = {"company": company.id, "a1": a1.id, "a2": a2.id, "emp": emp.id,
           "emails": [a1.email, a2.email, emp.email]}
    db.close()
    yield ids
    db = SessionLocal()
    from src.core.models import AuthSession, UserTotp
    db.execute(delete(Notification).where(
        Notification.company_id == ids["company"]))
    db.execute(delete(Notification).where(Notification.user_id.in_(
        [ids["a1"], ids["a2"], ids["emp"]])))
    db.execute(delete(Setting).where(Setting.key.in_([
        f"notifications.muted_kinds:{ids['a1']}",
        f"notifications.muted_kinds:{ids['emp']}",
    ])))
    db.execute(delete(im.NotificationRule).where(
        im.NotificationRule.company_id == ids["company"]))
    db.execute(delete(im.Connection).where(
        im.Connection.company_id == ids["company"]))
    db.execute(delete(crm.Activity).where(crm.Activity.created_by.in_(
        [ids["a1"], ids["emp"]])))
    db.execute(delete(crm.Deal).where(crm.Deal.company_id == ids["company"]))
    db.execute(delete(crm.Stage).where(crm.Stage.company_id == ids["company"]))
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


# ---------- Этап B: потребители событий ----------

def test_consumer_sales_order_created(org):
    consumer = _make_consumer("acc.sales.order.created", "sales_order_created")
    payload = {"order_id": f"o-{RUN}", "number": f"S-42-{RUN}", "status": "draft",
               "amount": "100.00", "currency": "RUB",
               "company_id": str(org["company"])}
    consumer(payload)
    consumer(payload)  # повтор события — dedup
    for uid in (org["a1"], org["a2"]):
        rows = [r for r in _rows(uid, "sales_order_created")
                if r.entity_id == f"o-{RUN}"]
        assert len(rows) == 1
        assert rows[0].link == "/crm/orders" and rows[0].severity == "info"
        assert RUN in rows[0].title
    assert _rows(org["emp"], "sales_order_created") == []


def test_consumer_online_payment_events(org):
    ok = _make_consumer("integration.payment.processed", "online_order")
    ok({"payment_id": f"pay-{RUN}", "provider_payment_id": "pp-1",
        "sales_order_id": f"o-{RUN}", "company_id": str(org["company"])})
    rows = _rows(org["a1"], "online_order")
    assert any(r.entity_id == f"o-{RUN}" and r.link == "/crm/orders"
               for r in rows)
    fail = _make_consumer("integration.payment.failed", "online_payment_failed")
    fail({"payment_id": f"pay-{RUN}", "reason": "no_items", "step": "items",
          "company_id": str(org["company"])})
    rows = _rows(org["a1"], "online_payment_failed")
    assert any(r.entity_id == f"pay-{RUN}" and r.severity == "critical"
               and r.link == "/integrations/payments" for r in rows)


def test_consumer_ai_proposal_personal(org):
    consumer = _make_consumer("ai.proposal.created", "ai_proposal")
    consumer({"proposal_id": f"pr-{RUN}", "action_type": "create_transaction",
              "company_id": str(org["company"]), "user_id": str(org["emp"])})
    consumer({"proposal_id": f"pr-{RUN}", "action_type": "create_transaction",
              "company_id": str(org["company"]), "user_id": str(org["emp"])})
    rows = [r for r in _rows(org["emp"], "ai_proposal")
            if r.entity_id == f"pr-{RUN}"]
    assert len(rows) == 1  # dedup
    assert rows[0].audience == "user" and rows[0].link == "/assistant"
    # автору — лично, админам не приходит
    assert _rows(org["a1"], "ai_proposal") == []
    # без user_id — пропуск (WARNING), строк нет
    before = len(_rows(org["emp"], "ai_proposal"))
    consumer({"proposal_id": "x", "company_id": str(org["company"])})
    assert len(_rows(org["emp"], "ai_proposal")) == before


def test_consumer_signup_platform_admins():
    consumer = _make_consumer("platform.signup.verified", "signup_request")
    key = f"signup-{RUN}"
    consumer({"signup_request_id": key, "company_name": f"ООО {RUN}",
              "contact_name": "Иван", "email": f"signup-{RUN}@t.local"})
    db = SessionLocal()
    try:
        pla = db.scalars(select(User).where(
            User.is_platform_admin.is_(True), User.is_active.is_(True))).all()
        rows = db.scalars(select(Notification).where(
            Notification.kind == "signup_request",
            Notification.entity_id == key)).all()
        assert len(rows) == len(pla) >= 1
        assert all(r.company_id is None for r in rows)  # платформенная строка
        assert all(r.link == "/select-org" for r in rows)
    finally:
        db = SessionLocal()
        db.execute(delete(Notification).where(
            Notification.kind == "signup_request", Notification.entity_id == key))
        db.commit()
        db.close()


# ---------- Этап B: CRM-просрочки ----------

def test_sweep_crm_overdue(org):
    from src.modules.mini_crm.tasks import sweep_crm_overdue
    db = SessionLocal()
    today = datetime.now(UTC).date()
    st_open = crm.Stage(company_id=org["company"], name=f"Переговоры {RUN}",
                         position=1, probability=30)
    st_won = crm.Stage(company_id=org["company"], name=f"Выиграна {RUN}",
                        position=9, is_won=True)
    db.add_all([st_open, st_won])
    db.flush()
    d_overdue = crm.Deal(company_id=org["company"], title=f"Просроч {RUN}",
                          stage_id=st_open.id, amount=100, currency="RUB",
                          expected_close_at=today - timedelta(days=2),
                          responsible_id=org["emp"], created_by=org["a1"])
    d_won = crm.Deal(company_id=org["company"], title=f"Вон {RUN}",
                      stage_id=st_won.id, amount=100, currency="RUB",
                      expected_close_at=today - timedelta(days=2),
                      responsible_id=org["emp"], created_by=org["a1"])
    db.add_all([d_overdue, d_won])
    db.flush()
    a_overdue = crm.Activity(deal_id=d_overdue.id, title=f"Звонок {RUN}",
                              due_at=today - timedelta(days=1), created_by=org["a1"])
    a_done = crm.Activity(deal_id=d_overdue.id, title=f"Встреча {RUN}",
                           due_at=today - timedelta(days=1), done=True,
                           done_at=datetime.now(UTC), created_by=org["a1"])
    db.add_all([a_overdue, a_done])
    db.commit()
    db.close()

    r1 = sweep_crm_overdue()
    assert r1["created"] >= 2
    deals = [r for r in _rows(org["emp"], "deal_overdue")
             if r.entity_id == str(d_overdue.id)]
    tasks = [r for r in _rows(org["emp"], "crm_task_overdue")
             if r.entity_id == str(a_overdue.id)]
    assert len(deals) == 1 and deals[0].link == "/crm/deals"
    assert len(tasks) == 1 and tasks[0].audience == "user"
    # won-стадия и выполненная задача — без уведомлений
    assert not [r for r in _rows(org["emp"], "deal_overdue")
                if r.entity_id == str(d_won.id)]
    assert not [r for r in _rows(org["emp"], "crm_task_overdue")
                if r.entity_id == str(a_done.id)]
    # повторный запуск в тот же день — dedup
    r2 = sweep_crm_overdue()
    assert len([r for r in _rows(org["emp"], "deal_overdue")
                if r.entity_id == str(d_overdue.id)]) == 1
    assert len([r for r in _rows(org["emp"], "crm_task_overdue")
                if r.entity_id == str(a_overdue.id)]) == 1
    assert r2["created"] == 0


# ---------- Этап C: каналы правил ----------

def _rule(company_id, event, channels):
    db = SessionLocal()
    rule = im.NotificationRule(
        company_id=company_id, name=f"ntbc-{RUN}-{len(channels)}",
        event_name=event, chat_id="-1001", template="x",
        channels=channels)
    db.add(rule)
    db.commit()
    db.close()
    return rule.id


def test_channels_gate_in_app(org):
    ev = "acc.sales.order.created"
    cid = str(org["company"])
    # без правил — in_app работает (умолчание)
    assert in_app_allowed(SessionLocal(), ev, cid) is True
    # правило только telegram → in_app глушится
    r1 = _rule(org["company"], ev, ["telegram"])
    db = SessionLocal()
    assert in_app_allowed(db, ev, cid) is False
    db.close()
    consumer = _make_consumer(ev, "sales_order_created")
    consumer({"order_id": f"o-g-{RUN}", "number": "N", "amount": "1",
              "currency": "RUB", "company_id": cid})
    assert not [r for r in _rows(org["a1"], "sales_order_created")
                if r.entity_id == f"o-g-{RUN}"]
    # оба канала → in_app создаётся
    db = SessionLocal()
    db.get(im.NotificationRule, r1).channels = ["in_app", "telegram"]
    db.commit()
    db.close()
    assert in_app_allowed(SessionLocal(), ev, cid) is True
    consumer({"order_id": f"o-g2-{RUN}", "number": "N", "amount": "1",
              "currency": "RUB", "company_id": cid})
    assert len([r for r in _rows(org["a1"], "sales_order_created")
                if r.entity_id in (f"o-g-{RUN}", f"o-g2-{RUN}")]) == 1


def test_rules_api_channels(client, org):
    h = _token(client, f"nt-bc1-{RUN}@t.local")
    r = client.post(f"{API}/integrations/notification-rules", headers=h, json={
        "name": f"ntbc-api-{RUN}", "event_name": "crm.deal.created",
        "chat_id": "-1002", "template": "deal {title}",
        "channels": ["in_app", "telegram"]})
    assert r.status_code == 201, r.text
    assert sorted(r.json()["channels"]) == ["in_app", "telegram"]
    # дефолт — только telegram (существующие правила не меняют поведения)
    r2 = client.post(f"{API}/integrations/notification-rules", headers=h, json={
        "name": f"ntbc-api2-{RUN}", "event_name": "crm.deal.lost",
        "chat_id": "-1002", "template": "lost"})
    assert r2.status_code == 201
    assert r2.json()["channels"] == ["telegram"]
    # неизвестный канал — 422
    r3 = client.post(f"{API}/integrations/notification-rules", headers=h, json={
        "name": "x", "event_name": "crm.deal.won", "chat_id": "1",
        "channels": ["sms"]})
    assert r3.status_code == 422


# ---------- Этап C: мьюты типов ----------

def test_muted_kinds_api_and_notify(client, org):
    h = _token(client, f"nt-bce-{RUN}@t.local")
    # неизвестный kind — 422
    r = client.put(f"{API}/notifications/muted-kinds", headers=h,
                   json={"kinds": ["nope"]})
    assert r.status_code == 422
    # мьют low_stock
    r = client.put(f"{API}/notifications/muted-kinds", headers=h,
                   json={"kinds": ["low_stock"]})
    assert r.json() == {"muted": ["low_stock"]}
    assert client.get(f"{API}/notifications/muted-kinds",
                      headers=h).json() == {"muted": ["low_stock"]}
    # замьюченный тип не создаётся, другой — создаётся
    db = SessionLocal()
    n1 = notify(db, company_id=org["company"], kind="low_stock",
                audience="user", user_id=org["emp"], title="muted",
                entity_id=f"m1-{RUN}", dedup_key=f"low_stock:m1:{RUN}")
    n2 = notify(db, company_id=org["company"], kind="sync_failed",
                audience="user", user_id=org["emp"], title="kept",
                entity_id=f"m2-{RUN}", dedup_key=f"sync:m2:{RUN}")
    db.commit()
    db.close()
    assert (n1, n2) == (0, 1)
    # снятие мьюта — тип снова создаётся
    client.put(f"{API}/notifications/muted-kinds", headers=h, json={"kinds": []})
    db = SessionLocal()
    n3 = notify(db, company_id=org["company"], kind="low_stock",
                audience="user", user_id=org["emp"], title="unmuted",
                entity_id=f"m1-{RUN}", dedup_key=f"low_stock:m1b:{RUN}")
    db.commit()
    db.close()
    assert n3 == 1
    # мьют личный: у админа (без мьюта) тот же тип создаётся
    db = SessionLocal()
    n4 = notify(db, company_id=org["company"], kind="low_stock",
                audience="user", user_id=org["a1"], title="admin",
                entity_id=f"m1-{RUN}", dedup_key=f"low_stock:m1c:{RUN}")
    db.commit()
    db.close()
    assert n4 == 1


# ---------- Этап C: telegram-connection по организации ----------

def test_pick_telegram_connection_company_scoped(org):
    db = SessionLocal()
    other = Company(name=f"NT-BC-O-{RUN}")
    db.add(other)
    db.flush()
    c_other = im.Connection(company_id=other.id, name="bot-other",
                             connector_code="telegram_bot",
                             credentials_enc="", config={})
    c_mine = im.Connection(company_id=org["company"], name="bot-mine",
                            connector_code="telegram_bot",
                            credentials_enc="", config={})
    db.add_all([c_other, c_mine])
    db.commit()
    try:
        picked = pick_telegram_connection(db, org["company"])
        assert picked.id == c_mine.id  # свой бот, а не «самый свежий глобально»
        picked_other = pick_telegram_connection(db, other.id)
        assert picked_other.id == c_other.id
    finally:
        db.execute(delete(im.Connection).where(
            im.Connection.id.in_([c_other.id, c_mine.id])))
        db.execute(delete(Company).where(Company.id == other.id))
        db.commit()
        db.close()


# ---------- Этап C: ретеншн ----------

def test_notifications_retention(org):
    from src.core.notifications import tasks as nt_tasks
    db = SessionLocal()
    old_read = Notification(company_id=org["company"], user_id=org["a1"],
                            audience="user", kind="sync_failed", severity="info",
                            title="old-read", read_at=datetime.now(UTC) - timedelta(days=100),
                            dedup_key=f"ret:or:{RUN}")
    old_unread = Notification(company_id=org["company"], user_id=org["a1"],
                              audience="user", kind="sync_failed", severity="info",
                              title="old-unread",
                              dedup_key=f"ret:ou:{RUN}")
    fresh_read = Notification(company_id=org["company"], user_id=org["a1"],
                              audience="user", kind="sync_failed", severity="info",
                              title="fresh-read", read_at=datetime.now(UTC),
                              dedup_key=f"ret:fr:{RUN}")
    db.add_all([old_read, old_unread, fresh_read])
    db.commit()
    db.close()
    with patch.object(nt_tasks, "get_settings",
                      lambda: SimpleNamespace(notifications_retention_days=90)):
        r = nt_tasks.notifications_retention()
    assert r["deleted"] >= 1
    db = SessionLocal()
    left = {n.dedup_key for n in db.scalars(select(Notification).where(
        Notification.dedup_key.like(f"ret:%:{RUN}"))).all()}
    db.close()
    assert left == {f"ret:ou:{RUN}", f"ret:fr:{RUN}"}  # непрочитанные не трогаем
    # 0 — чистка выключена
    with patch.object(nt_tasks, "get_settings",
                      lambda: SimpleNamespace(notifications_retention_days=0)):
        assert nt_tasks.notifications_retention() == {"deleted": 0}


# ---------- Tauri-контракт (§9): unread-count лёгкий и стабильный ----------

def test_tauri_unread_count_contract(client, org):
    h = _token(client, f"nt-bc2-{RUN}@t.local")
    r = client.get(f"{API}/notifications/unread-count", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"count"} and isinstance(body["count"], int)
