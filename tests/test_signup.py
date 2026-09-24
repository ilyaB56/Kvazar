"""Самообслуживание клиентов (блок 2): публичная регистрация.

Цикл: signup → письмо с токеном (mock-SMTP) → verify (одноразово) →
уведомление платформенному админу → одобрение → организация + вход по
паролю из заявки. Rate limit 3/час, слабый пароль → 422.

Запуск: docker compose exec api pytest tests/test_signup.py
"""

from __future__ import annotations

import os
import socketserver
import threading
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
API = "/api/v1"
RUN = uuid.uuid4().hex[:8]

pytestmark = pytest.mark.integration

MAILBOX: list[str] = []


def _start_mock_smtp():
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.sendall(b"220 mock\r\n")
            data, got, buf = b"", False, b""
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if not got:
                        if line.upper().startswith(b"DATA"):
                            self.request.sendall(b"354 go\r\n")
                            got = True
                        elif line.upper().startswith(b"QUIT"):
                            self.request.sendall(b"221 bye\r\n")
                            self.request.close()
                            return
                        else:
                            self.request.sendall(b"250 ok\r\n")
                    elif line == b".":
                        MAILBOX.append(data.decode("utf-8", errors="replace"))
                        data, got = b"", False
                        self.request.sendall(b"250 accepted\r\n")
                    else:
                        data += line + b"\n"

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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


def _platform(client):
    """Платформенный токен + контекст org (создание SMTP-коннекта
    требует company; сам коннектор станет платформенным через БД)."""
    login = client.post(f"{API}/auth/login", json={
        "email": "admin@example.com",
        "password": os.environ.get("ERP_ADMIN_PASSWORD", "admin12345")}).json()
    pair = client.post(f"{API}/auth/select-org", json={
        "refresh_token": login["refresh_token"],
        "company_id": login["organizations"][0]["id"]}).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


def _token_from_db(email: str) -> str:
    """Токен — из БД (расшифровка Fernet); доставка письма платформенным
    SMTP проверяется живой приёмкой smoke (как в test_multitenancy_d)."""
    from sqlalchemy import text

    from src.core.signup import recovery_token
    from src.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT id FROM erp_core.signup_requests WHERE email = :e"
            " AND status = 'pending' ORDER BY created_at DESC LIMIT 1"
        ).bindparams(e=email)).first()
        assert row, "pending-заявка не найдена"
        from src.core.models import SignupRequest
        return recovery_token(db.get(SignupRequest, row[0]))
    finally:
        db.close()


def test_signup_full_cycle(client):
    plat = _platform(client)
    email = f"founder-{RUN}@signup.test"
    try:
        # слабый пароль → 422
        weak = client.post(f"{API}/auth/signup", json={
            "company_name": "ООО Стартап", "name": "Основатель",
            "email": email, "password": "short"})
        assert weak.status_code == 422

        # заявка → 200 всегда; токен ждём в заявке
        ok = client.post(f"{API}/auth/signup", json={
            "company_name": f"ООО Стартап {RUN}", "name": "Основатель",
            "email": email, "password": "Strong1pass"})
        assert ok.status_code == 200 and ok.json()["ok"]

        # повторная заявка тем же email — тоже 200 (перевыпуск токена,
        # поэтому читаем токен из БД строго ПОСЛЕ повтора)
        again = client.post(f"{API}/auth/signup", json={
            "company_name": f"ООО Стартап {RUN}", "name": "Основатель",
            "email": email, "password": "Strong1pass"})
        assert again.status_code == 200
        token = _token_from_db(email)

        # verify: после — verified; уведомление админу шлёт платформенный
        # SMTP (покрыто smoke), здесь — контракт статусов
        verify = client.post(f"{API}/auth/signup/verify", json={"token": token})
        assert verify.status_code == 200, verify.text
        # токен одноразовый
        reuse = client.post(f"{API}/auth/signup/verify", json={"token": token})
        assert reuse.status_code == 410
        # неизвестный токен
        unknown = client.post(f"{API}/auth/signup/verify",
                              json={"token": "x" * 43})
        assert unknown.status_code == 410
        requests_list = client.get(f"{API}/platform/signup-requests",
                                   headers=plat).json()
        mine = [r for r in requests_list if r["email"] == email]
        assert mine and mine[0]["status"] == "verified"

        # одобрение → организация, админ входит по паролю из заявки
        approve = client.post(
            f"{API}/platform/signup-requests/{mine[0]['id']}/approve",
            headers=plat)
        assert approve.status_code == 200, approve.text
        org = approve.json()
        assert org["name"] == f"ООО Стартап {RUN}"
        assert org["admin"]["email"] == email

        login = client.post(f"{API}/auth/login", json={
            "email": email, "password": "Strong1pass"})
        assert login.status_code == 200, login.text

        # повторное одобрение → 409
        reapprove = client.post(
            f"{API}/platform/signup-requests/{mine[0]['id']}/approve",
            headers=plat)
        assert reapprove.status_code == 409
    finally:
        _cleanup(email)


def test_signup_reject_and_rate_limit(client):
    plat = _platform(client)
    email = f"reject-{RUN}@signup.test"
    try:
        ok = client.post(f"{API}/auth/signup", json={
            "company_name": "ОООтказ", "name": "", "email": email,
            "password": "Strong1pass"})
        assert ok.status_code == 200

        requests_list = client.get(f"{API}/platform/signup-requests",
                                   headers=plat).json()
        mine = [r for r in requests_list if r["email"] == email]
        assert mine and mine[0]["status"] == "pending"

        # отклонение неподтверждённой — можно
        reject = client.post(
            f"{API}/platform/signup-requests/{mine[0]['id']}/reject",
            headers=plat)
        assert reject.status_code == 200
        # повторно — 409
        assert client.post(
            f"{API}/platform/signup-requests/{mine[0]['id']}/reject",
            headers=plat).status_code == 409

        # rate limit: 3 заявки/час с одного email|ip; 4-я (после двух выше
        # в этом тесте) — 429. Новый email — чистый счётчик.
        for _ in range(3):
            client.post(f"{API}/auth/signup", json={
                "company_name": "ОООтказ", "name": "", "email": email,
                "password": "Strong1pass"})
        limited = client.post(f"{API}/auth/signup", json={
            "company_name": "ОООтказ", "name": "", "email": email,
            "password": "Strong1pass"})
        assert limited.status_code == 429
    finally:
        _cleanup(email)


def _cleanup(email: str):

    from sqlalchemy import text

    from src.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT id, org_id FROM erp_core.signup_requests"
            " WHERE email = :e").bindparams(e=email)).first()
        if row and row[1]:
            db.execute(text(
                "UPDATE erp_core.companies SET is_active = false"
                " WHERE id = :o").bindparams(o=row[1]))
            db.execute(text(
                "UPDATE erp_core.users SET is_active = false"
                " WHERE email = :e").bindparams(e=email))
        db.execute(text(
            "UPDATE erp_core.signup_requests SET status = 'rejected'"
            " WHERE email = :e AND status IN ('pending', 'verified')"
        ).bindparams(e=email))
        db.commit()
    finally:
        db.close()
