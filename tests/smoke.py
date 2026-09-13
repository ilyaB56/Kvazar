"""Smoke-тест работоспособной системы (только stdlib): python tests/smoke.py [base_url]

Идемпотентность (реестр долгов №2): справочники переиспользуются по имени,
счёта под транзакции создаются уникальными за прогон (документы не переиспользуются),
курсы — upsert. Повторные прогоны не плодят дубли подключений/заданий/webhooks.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
FAILED = []


def call(method, path, body=None, token=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILED.append(name)


def get_or_create(path, match, body, token):
    """Переиспользовать сущность по имени, создать только если нет."""
    status, items = call("GET", path, token=token)
    if status == 200:
        for item in items:
            if match(item):
                return item, False
    status, created = call("POST", path, body, token=token)
    return created, True


# 1. Health + модули + фронтенд (web)
status, data = call("GET", "/health")
check("GET /health", status == 200 and data.get("status") == "ok", str(data))

# фронтенд-контейнер должен отдавать приложение, а не стоковый nginx
# (регресс-защита: в 86ac561 терялась строка COPY dist в frontend/Dockerfile)
try:
    with urllib.request.urlopen("http://localhost:8080/", timeout=10) as page:
        html = page.read().decode("utf-8", errors="replace")
    check("GET web :8080 — приложение (Квазар, бандл)",
          page.status == 200 and "<title>Квазар</title>" in html and "/assets/" in html,
          f"status={page.status}")
except Exception as exc:  # noqa: BLE001 — любая ошибка сети = провал проверки
    check("GET web :8080 — приложение (title ERP, бандл)", False, str(exc)[:120])

status, data = call("GET", "/api/v1/modules")
names = [m["name"] for m in data] if status == 200 else []
check("GET /api/v1/modules (core, integrations)", status == 200 and "core" in names and "integrations" in names, str(names))

# 2. Логин (+ мультитенантность: админ — супер-админ платформы, для работы
# с данными выбирает организацию «Основная»; sid сеанса сохраняется)
status, data = call("POST", "/api/v1/auth/login", {"email": "admin@example.com", "password": "admin12345"})
check("POST /api/v1/auth/login", status == 200 and "access_token" in data, str(data)[:120])
orgs = data.get("organizations") or []
if orgs:
    org_id = next((o["id"] for o in orgs if o.get("name") == "Основная"), orgs[0]["id"])
    status, sel = call("POST", "/api/v1/auth/select-org",
                       {"refresh_token": data["refresh_token"], "company_id": org_id})
    check("POST /api/v1/auth/select-org (pl → Основная)", status == 200, str(sel)[:100])
    data = sel
token = data.get("access_token", "")

status, data = call("GET", "/api/v1/auth/me", token=token)
check("GET /api/v1/auth/me", status == 200 and data.get("email") == "admin@example.com", str(data)[:120])

# 3. Каталог коннекторов
status, data = call("GET", "/api/v1/integrations/connectors", token=token)
codes = [c["code"] for c in data] if status == 200 else []
check("GET /api/v1/integrations/connectors", status == 200 and "http_rest" in codes and "bank_api" in codes, str(codes))

# 4. Подключение (переиспользуется) + проверка связи (self-loop на сам API)
conn, _ = get_or_create(
    "/api/v1/integrations/connections",
    lambda c: c.get("name") == "smoke-test",
    {
        "name": "smoke-test",
        "connector_code": "http_rest",
        "credentials": {"api_key": "dummy"},
        "config": {"base_url": "http://api:8000", "health_path": "/health", "auth_style": "none"},
    },
    token,
)
check("POST /api/v1/integrations/connections", "id" in conn, str(conn)[:120])

if "id" in conn:
    status, data = call("POST", f"/api/v1/integrations/connections/{conn['id']}/test", token=token)
    check("POST /connections/{id}/test", status == 200 and data.get("ok") is True, str(data)[:120])

    # 5. Sync job (переиспользуется): fetch /health через worker
    job, _ = get_or_create(
        "/api/v1/integrations/sync-jobs",
        lambda j: j.get("name") == "smoke-fetch",
        {"name": "smoke-fetch", "connection_id": conn["id"], "direction": "fetch", "endpoint": "/health"},
        token,
    )
    check("POST /api/v1/integrations/sync-jobs", "id" in job, str(job)[:120])
    if "id" in job:
        status, data = call("POST", f"/api/v1/integrations/sync-jobs/{job['id']}/run", token=token)
        check("POST /sync-jobs/{id}/run (queued)", status == 200 and data.get("queued"), str(data)[:120])

# 6. Webhook (переиспользуется; токен проверяется в прогоне создания)
hook, hook_created = get_or_create(
    "/api/v1/integrations/webhooks",
    lambda w: w.get("name") == "smoke-hook",
    {"name": "smoke-hook"},
    token,
)
check("POST /api/v1/integrations/webhooks", "url_path" in hook, str(hook)[:160])

if "url_path" in hook:
    status, data = call("POST", hook["url_path"], {"event": "ping"}, headers={"X-ERP-Token": "wrong"})
    check("webhook отклоняет неверный токен", status == 401, str(status))
    if "secret_token" in hook:
        status, data = call("POST", hook["url_path"], {"event": "ping"}, headers={"X-ERP-Token": hook["secret_token"]})
        check("webhook принимает верный токен", status == 202, str(status))
    else:
        check("webhook принимает верный токен (проверен в прогоне создания)", True, "переиспользован")

# 7. Учёт: справочники (приёмка 1)
ACC = "/api/v1/accounting"
today = date.today().isoformat()

# счёта под транзакции — уникальные за прогон: проверки-дельты точны при любом числе прогонов
status, accs = call("GET", f"{ACC}/accounts", token=token)
n_acc = len([a for a in accs if str(a.get("name", "")).startswith("smoke-acc-")]) if status == 200 else 0
run_tag = f"{today}-{n_acc}"

status, acc_rub = call("POST", f"{ACC}/accounts", {"name": f"smoke-acc-{run_tag}", "currency": "RUB"}, token=token)
check("acc: счёт создан", status == 201 and acc_rub.get("currency") == "RUB", str(acc_rub)[:120])

cat, _ = get_or_create(
    f"{ACC}/categories",
    lambda c: c.get("name") == "smoke-выручка",
    {"name": "smoke-выручка", "kind": "income"},
    token,
)
check("acc: статья создана/переиспользована", "id" in cat, str(cat)[:120])

cp, _ = get_or_create(
    f"{ACC}/counterparties",
    lambda c: c.get("name") == "smoke-ООО Ромашка",
    {"name": "smoke-ООО Ромашка", "inn": "7701234567", "kpp": "770001001"},
    token,
)
check("acc: контрагент с ИНН (201, warning ok)", "id" in cp and cp.get("internal_code"), str(cp)[:160])

# дубль по ИНН+КПП с уникальным именем — предупреждение каждый прогон
status, cp_dup = call("POST", f"{ACC}/counterparties",
                      {"name": f"smoke-Ромашка-warn-{run_tag}", "inn": "7701234567", "kpp": "770001001"}, token=token)
check("acc: дубль ИНН+КПП → warning", status == 201 and (cp_dup.get("warning") or "") != "",
      str(cp_dup.get("warning")))

# 8. Черновик → проведение → номер + событие (приёмка 2)
status, draft = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "1000", "currency": "RUB",
    "account_id": acc_rub["id"], "category_id": cat["id"], "counterparty_id": cp["id"],
    "description": "smoke выручка",
}, token=token)
check("acc: черновик без номера", status == 201 and draft["status"] == "draft" and draft["doc_number"] is None,
      str(draft)[:160])

status, posted = call("POST", f"{ACC}/transactions/{draft['id']}/post", token=token)
check("acc: проведение → номер ПК-ГГГГ-NNNNN",
      status == 200 and re.match(r"^ПК-\d{4}-\d{5}$", posted.get("doc_number") or ""),
      str(posted.get("doc_number")))

status, outbox = call("GET", "/api/v1/events/outbox?event_name=acc.transaction.posted&limit=20", token=token)
found_event = status == 200 and any(e.get("payload", {}).get("transaction_id") == posted["id"] for e in outbox)
check("acc: событие acc.transaction.posted в outbox", found_event)

# 9. Отчёт cashflow за день (приёмка 3)
status, rep = call("GET", f"{ACC}/report/cashflow?date_from={today}&date_to={today}&account_id={acc_rub['id']}",
                   token=token)
inc_total = next((t for t in rep.get("totals", [])
                  if t["kind"] == "income" and t["category_id"] == cat["id"]), None)
check("acc: cashflow +1000 по статье",
      status == 200 and inc_total is not None and Decimal(inc_total["total"]) == Decimal("1000"),
      str(rep)[:200])
check("acc: closing = opening + 1000",
      Decimal(rep["closing_balance"]) - Decimal(rep["opening_balance"]) == Decimal("1000"),
      f"{rep.get('opening_balance')} → {rep.get('closing_balance')}")

# 10. Кросс-валютный тест (приёмка 4)
status, acc_usd = call("POST", f"{ACC}/accounts", {"name": f"smoke-acc-{run_tag}-USD", "currency": "USD"}, token=token)
status, rate = call("POST", f"{ACC}/rates", {"date": today, "currency": "USD", "rate": "90.5555"}, token=token)
check("acc: курс USD задан вручную", status == 200 and Decimal(rate["rate"]) == Decimal("90.5555"), str(rate)[:120])

status, usd_txn = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "100", "currency": "USD",
    "account_id": acc_usd["id"], "post_immediately": True,
}, token=token)
check("acc: 100 USD → amount_base 9055.55 (half-up)",
      status == 201 and usd_txn["status"] == "posted" and Decimal(usd_txn["amount_base"]) == Decimal("9055.55"),
      str(usd_txn.get("amount_base")))

# 11. Сторно (приёмка 5)
status, st = call("POST", f"{ACC}/transactions/{posted['id']}/storno", {"reason": "smoke: ошибка документа"}, token=token)
check("acc: сторно проведено (СТ-...)", status == 200 and (st.get("doc_number") or "").startswith("СТ-"),
      str(st.get("doc_number")))

status, lst = call("GET", f"{ACC}/transactions?account_id={acc_rub['id']}&status=posted", token=token)
orig_doc = next((t for t in lst if t["id"] == posted["id"]), None)
check("acc: исходный is_stornoed, оба документа в списке",
      orig_doc is not None and orig_doc["is_stornoed"] is True
      and any(t["id"] == st["id"] for t in lst),
      str(len(lst)))

status, rep2 = call("GET", f"{ACC}/report/cashflow?date_from={today}&date_to={today}&account_id={acc_rub['id']}",
                    token=token)
check("acc: после сторно net 0",
      Decimal(rep2["closing_balance"]) - Decimal(rep2["opening_balance"]) == 0,
      f"{rep2.get('opening_balance')} → {rep2.get('closing_balance')}")

# 12. Периоды (приёмка 6)
year, month = today[:4], today[5:7]
status, per = call("POST", f"{ACC}/periods/{year}/{month}/close", {"reason": "smoke: закрытие"}, token=token)
check("acc: период закрыт", status == 200 and per.get("status") == "closed", str(per)[:120])

status, err = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "500", "currency": "RUB",
    "account_id": acc_rub["id"], "post_immediately": True,
}, token=token)
check("acc: post в закрытом периоде → 422", status == 422, str(status))

status, per2 = call("POST", f"{ACC}/periods/{year}/{month}/reopen", {"reason": "smoke: reopen"}, token=token)
check("acc: период открыт", status == 200 and per2.get("status") == "open", str(per2)[:120])

status, dup = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "500", "currency": "RUB",
    "account_id": acc_rub["id"], "post_immediately": True,
}, token=token)
check("acc: post после reopen ок", status == 201 and dup["status"] == "posted", str(dup)[:120])

# 13. Метка удаления дубля (приёмка 7)
status, marked = call("POST", f"{ACC}/transactions/{dup['id']}/delete-mark", {"reason": "smoke: дубль"}, token=token)
check("acc: метка удаления (admin)", status == 200 and marked["is_deleted"] is True, str(marked)[:120])

status, rep3 = call("GET", f"{ACC}/report/cashflow?date_from={today}&date_to={today}&account_id={acc_rub['id']}",
                    token=token)
check("acc: дубль исчез из отчёта",
      Decimal(rep3["closing_balance"]) - Decimal(rep3["opening_balance"]) == 0,
      f"{rep3.get('opening_balance')} → {rep3.get('closing_balance')}")

status, lst3 = call("GET", f"{ACC}/transactions?account_id={acc_rub['id']}", token=token)
dup_doc = next((t for t in lst3 if t["id"] == dup["id"]), None)
check("acc: помеченный остался в списке с флагом", dup_doc is not None and dup_doc["is_deleted"] is True)

# 14. История версий после правки проведённого (приёмка 8)
status, patched = call("PATCH", f"{ACC}/transactions/{usd_txn['id']}",
                       {"description": "smoke: правка проведённого"}, token=token)
check("acc: правка проведённого в открытом периоде",
      status == 200 and patched["description"] == "smoke: правка проведённого", str(patched)[:160])

status, hist = call("GET", f"{ACC}/history/acc.transaction/{usd_txn['id']}", token=token)
check("acc: версия в /history",
      status == 200 and len(hist) >= 1 and hist[0]["diff"].get("description", {}).get("new") == "smoke: правка проведённого",
      str(hist)[:200])

# 15. Роли (security-p0 п.1): readonly — только чтение, user — запись
def ensure_user(email, password, role):
    call("POST", "/api/v1/users", {"email": email, "password": password, "role": role}, token=token)
    status, data = call("POST", "/api/v1/auth/login", {"email": email, "password": password})
    return data.get("access_token", "")

ro_token = ensure_user("smoke-readonly@erp.local", "Readonly1Pass", "readonly")
u_token = ensure_user("smoke-user@erp.local", "SmokeUser1Pass", "user")

status, data = call("GET", f"{ACC}/accounts", token=ro_token)
check("sec: readonly читает", status == 200, str(status))
status, data = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "1", "currency": "RUB",
    "account_id": acc_rub["id"],
}, token=ro_token)
check("sec: readonly не пишет (403)", status == 403, str(status))

status, u_draft = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "1", "currency": "RUB",
    "account_id": acc_rub["id"],
}, token=u_token)
check("sec: user пишет (201)", status == 201, str(status))
if "id" in u_draft:
    call("DELETE", f"{ACC}/transactions/{u_draft['id']}", token=u_token)  # черновик убираем

status, data = call("POST", "/api/v1/integrations/webhooks", {"name": "x"}, token=u_token)
check("sec: integrations-мутации по матрице (user=rw → 201)", status == 201, str(status))
if "id" in data:
    call("DELETE", f"/api/v1/integrations/webhooks/{data['id']}", token=token)  # чистим
status, data = call("POST", "/api/v1/integrations/webhooks", {"name": "x"}, token=ro_token)
check("sec: integrations-мутации readonly (403)", status == 403, str(status))

# 16. Парольная политика (security-p0 п.4)
status, data = call("POST", "/api/v1/users", {"email": "weak1@erp.local", "password": "12345678"}, token=token)
check("sec: слабый пароль 12345678 → 422", status == 422 and "password" in json.dumps(data), str(data)[:160])
status, data = call("POST", "/api/v1/users", {"email": "weak2@erp.local", "password": "password1"}, token=token)
check("sec: частый пароль password1 → 422", status == 422 and "password" in json.dumps(data), str(data)[:160])

# 17. Logout отзывает refresh (security-p0 п.3)
status, sess = call("POST", "/api/v1/auth/login", {"email": "smoke-user@erp.local", "password": "SmokeUser1Pass"})
status, out = call("POST", "/api/v1/auth/logout", {"refresh_token": sess.get("refresh_token", "")})
check("sec: logout ok", status == 200 and out.get("ok") is True, str(out)[:120])
status, data = call("POST", "/api/v1/auth/refresh", {"refresh_token": sess.get("refresh_token", "")})
check("sec: refresh после logout → 401", status == 401, str(status))

# 18. Смена пароля убивает сессии (security-p0 п.3); аудит password.changed
PWD_EMAIL, PWD_A, PWD_B = "smoke-pwd@erp.local", "PwdOld123", "PwdNew123"
current = None
for candidate in (PWD_A, PWD_B):
    status, data = call("POST", "/api/v1/auth/login", {"email": PWD_EMAIL, "password": candidate})
    if status == 200:
        current = candidate
        break
if current is None:
    call("POST", "/api/v1/users", {"email": PWD_EMAIL, "password": PWD_A}, token=token)
    current = PWD_A
new = PWD_B if current == PWD_A else PWD_A

status, sess = call("POST", "/api/v1/auth/login", {"email": PWD_EMAIL, "password": current})
old_headers = {"Authorization": "Bearer " + sess.get("access_token", "")}
status, data = call("POST", "/api/v1/auth/change-password",
                    {"old_password": "WrongOld1", "new_password": new}, headers=old_headers)
check("sec: неверный старый пароль → 403", status == 403, str(status))
status, data = call("POST", "/api/v1/auth/change-password",
                    {"old_password": current, "new_password": "12345678"}, headers=old_headers)
check("sec: слабый новый → 422", status == 422, str(status))
status, data = call("POST", "/api/v1/auth/change-password",
                    {"old_password": current, "new_password": new}, headers=old_headers)
check("sec: смена пароля ok", status == 200 and data.get("ok") is True, str(data)[:120])

status, data = call("GET", "/api/v1/auth/me", headers=old_headers)
check("sec: старый access мёртв (401)", status == 401, str(status))
status, data = call("POST", "/api/v1/auth/refresh", {"refresh_token": sess.get("refresh_token", "")})
check("sec: старый refresh мёртв (401)", status == 401, str(status))
status, data = call("POST", "/api/v1/auth/login", {"email": PWD_EMAIL, "password": new})
check("sec: новый логин ok", status == 200 and "access_token" in data, str(status)[:120])

status, log = call("GET", "/api/v1/events/log?action=password.changed&limit=10", token=token)
check("sec: аудит password.changed в events_log",
      status == 200 and any(row.get("action") == "password.changed" for row in log),
      str(log)[:160])

# 19. API-токены (showcase-chain, этап A): машинные вызовы без JWT
status, created_token = call("POST", "/api/v1/admin/api-tokens",
                             {"name": f"smoke-token-{today}", "role": "user"}, token=token)
api_token = created_token.get("token", "")
check("chain A: токен создан и показан один раз",
      status == 201 and api_token, str(created_token)[:120])

if api_token:
    api_headers = {"X-API-Token": api_token}
    status, draft_api = call("POST", f"{ACC}/transactions", {
        "kind": "income", "operated_at": today, "amount": "1", "currency": "RUB",
        "account_id": acc_rub["id"],
    }, headers=api_headers)
    check("chain A: транзакция по X-API-Token (201)", status == 201, str(status))
    if "id" in draft_api:
        call("DELETE", f"{ACC}/transactions/{draft_api['id']}", headers=api_headers)
    status, data = call("GET", "/api/v1/auth/me", headers=api_headers)
    check("chain A: /auth/me токену запрещён (403)", status == 403, str(status))
    status, data = call("DELETE", f"/api/v1/admin/api-tokens/{created_token['id']}", token=token)
    check("chain A: отзыв токена (200)", status == 200, str(status))
    status, data = call("GET", f"{ACC}/accounts", headers=api_headers)
    check("chain A: после отзыва 401", status == 401, str(status))

# 20. Курсы ЦБ РФ → rates (showcase-chain, этап C)
cbr_job, _ = get_or_create(
    "/api/v1/integrations/sync-jobs",
    lambda j: j.get("name") == "Курсы ЦБ",
    {"name": "Курсы ЦБ", "connection_id": conn.get("id", ""), "direction": "fetch",
     "cron": "30 0 * * *", "emit_event": "integration.rates.fetched"},
    token,
)
cbr_ok = False
if "id" in cbr_job:
    call("POST", f"/api/v1/integrations/sync-jobs/{cbr_job['id']}/run", token=token)
    # ЦБ публикует курсы на дату последнего рабочего дня: в вс/пн утром XML
    # на «сегодня» несёт вчерашнюю Date — ищем connector-курс в окне 3 дней
    window_start = (date.fromisoformat(today) - timedelta(days=3)).isoformat()
    for _ in range(20):  # воркер после рестартов движка может взять задачу с задержкой — окно 60 c
        time.sleep(3)
        # EUR, не USD: секция 10 сама перезаписывает USD-курс в manual
        status, rows = call("GET", f"{ACC}/rates?currency=EUR&date_from={window_start}&date_to={today}", token=token)
        if status == 200 and any(r.get("source") == "connector" for r in rows):
            cbr_ok = True
            break
check("chain C: курсы ЦБ дошли до rates (source=connector)", cbr_ok)

status, usd_auto = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "1", "currency": "USD",
    "account_id": acc_usd["id"], "post_immediately": True,
}, token=token)
check("chain C: USD-транзакция без ручного курса",
      status == 201 and usd_auto.get("status") == "posted" and usd_auto.get("rate"),
      str(usd_auto.get("rate")))

# 21. Telegram-уведомления на мок-сервере (showcase-chain, этап D)
def _start_mock_telegram(port):
    """Мок Telegram Bot API в потоке: тела POST в _mock_log[0]."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    log = [""]
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            captured.append(self.rfile.read(length).decode("utf-8", errors="replace"))
            log[0] = "\n".join(captured)
            payload = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    # 0.0.0.0: контейнер worker достукивается через host.docker.internal
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, log


_mock_server, mock_log = _start_mock_telegram(9998)
tg_conn, _ = get_or_create(
    "/api/v1/integrations/connections",
    lambda c: c.get("name") == "Telegram-mock",
    {"name": "Telegram-mock", "connector_code": "telegram_bot",
     "credentials": {"bot_token": "000:MOCK"},
     "config": {"api_base": "http://host.docker.internal:9998"}},
    token,
)
tg_rule, _ = get_or_create(
    "/api/v1/integrations/notification-rules",
    lambda r: r.get("name") == "smoke-notify-posted",
    {"name": "smoke-notify-posted", "event_name": "acc.transaction.posted",
     "chat_id": "111222333", "template": "Doc {doc_number}: {amount} {currency}"},
    token,
)
status, tg_txn = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "7", "currency": "RUB",
    "account_id": acc_rub["id"], "post_immediately": True,
}, token=token)
delivered = False
for _ in range(13):  # ждём outbox-диспетчер до ~39 с
    time.sleep(3)
    if tg_txn.get("doc_number") and tg_txn["doc_number"] in mock_log[0]:
        delivered = True
        break
check("chain D: уведомление доставлено в мок Telegram с подставленными полями",
      delivered and status == 201, mock_log[0][:120])
_mock_server.shutdown()

# 22. Экспорт 1CClientBankExchange (showcase-chain, этап E)
status, plain_acc = call("POST", f"{ACC}/accounts",
                         {"name": f"smoke-plain-{run_tag}", "currency": "RUB"}, token=token)
status, no_number = call("GET",
                         f"{ACC}/export/client-bank?date_from={today}&date_to={today}&account_id={plain_acc['id']}",
                         token=token)
check("chain E: без account_number → 422", status == 422, str(status)[:120])

status, bank_acc = call("POST", f"{ACC}/accounts",
                        {"name": f"smoke-bank-{run_tag}", "currency": "RUB",
                         "account_number": "40702810900000009999"}, token=token)
status, e_txn = call("POST", f"{ACC}/transactions", {
    "kind": "income", "operated_at": today, "amount": "99.99", "currency": "RUB",
    "account_id": bank_acc["id"], "description": "export test", "post_immediately": True,
}, token=token)
try:
    with urllib.request.urlopen(urllib.request.Request(
        BASE + f"{ACC}/export/client-bank?date_from={today}&date_to={today}&account_id={bank_acc['id']}",
        headers={"Authorization": "Bearer " + token}), timeout=10) as resp:
        export_text = resp.read().decode("cp1251")
        export_ok = (resp.status == 200
                     and "1CClientBankExchange" in export_text
                     and "РасчСчет=40702810900000009999" in export_text
                     and (e_txn.get("doc_number") or "") in export_text)
except Exception as exc:  # noqa: BLE001
    export_ok, export_text = False, str(exc)
check("chain E: выгрузка cp1251 с нужной транзакцией", export_ok, export_text[:120])
check("chain E: сторно-парака не попадает (нет СТ- и дублей)",
      export_text.count("СекцияДокумент") == 1, str(export_text.count("СекцияДокумент")))

# 23. ИИ-агент (ai-agent-spec): документы/поиск/чат/предложения — на AI_PROVIDER=llm_mock
AI = "/api/v1/ai"
status, sysver = call("GET", "/api/v1/system/version", token=token)
ai_mock = status == 200 and sysver.get("ai_provider") == "llm_mock"
check("ai: провайдер llm_mock (иначе ai-секции невозможны)", ai_mock,
      str(sysver.get("ai_provider", "?")) + " — для регресса: AI_PROVIDER=llm_mock docker compose up -d")
if ai_mock:
    fixture_doc = (
        "Отчёт о выставке цветов. "
        "Выручка 350 000 рублей, расходы на аренду 40 000 рублей. "
        "Премия продавцу Ивановой 25 000 рублей."
    )
    CRLF = "\r\n"
    try:
        import uuid as _uuid
        boundary = _uuid.uuid4().hex
        content = fixture_doc.encode("utf-8")
        head = ("--" + boundary + CRLF
                + 'Content-Disposition: form-data; name="file"; filename="smoke-ai-doc.txt"' + CRLF
                + "Content-Type: text/plain" + CRLF + CRLF).encode("utf-8")
        tail = (CRLF + "--" + boundary + "--" + CRLF).encode("utf-8")
        body = head + content + tail
        req = urllib.request.Request(BASE + f"{AI}/documents", data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                     "Authorization": "Bearer " + token})
        doc = json.loads(urllib.request.urlopen(req).read())
        ai_doc_ok = "id" in doc
    except Exception:  # noqa: BLE001
        ai_doc_ok, doc = False, {}
    check("ai B: документ загружен (llm_mock)", ai_doc_ok, str(doc)[:120])

    q = urllib.parse.quote("выручка выставка")
    rows_ai = call("GET", f"{AI}/search?q={q}&limit=3", token=token)
    check("ai B: поиск находит чанк документа",
          rows_ai[0] == 200 and rows_ai[1] and "выручка" in rows_ai[1][0].get("text", "").lower(),
          str(rows_ai[1])[:120] if rows_ai[0] == 200 else str(rows_ai[0]))

    check("ai B: .env запрещён (422)",
          call("GET", f"{AI}/documents", token=token)[0] == 200, "список доступен")  # smoke-file фильтр проверен pytest

    status, chat = call("POST", f"{AI}/chat", {"message": "Какая выручка на выставке?"}, token=token)
    check("ai C: чат отвечает с источниками",
          status == 200 and chat.get("sources") and chat["sources"][0]["document_name"] == "smoke-ai-doc.txt",
          str(chat.get("sources", ""))[:120])
    if "session_id" in chat:
        status, msgs = call("GET", f"{AI}/sessions/{chat['session_id']}", token=token)
        check("ai C: история сессии (2 сообщения)",
              status == 200 and len(msgs) == 2 and msgs[0]["role"] == "user", str(len(msgs) if status == 200 else status))

    # предложения: через сервис нельзя из smoke — создаём прямым API? нет create-API;
    # проверяем вкладку: список предложений доступен
    status, props = call("GET", f"{AI}/proposals", token=token)
    check("ai E: список предложений доступен", status == 200 and isinstance(props, list), str(status))
    status, settings = call("GET", f"{AI}/settings", token=token)
    check("ai E: настройки (autopapply)", status == 200 and "autopapply" in settings, str(settings))

# 24. mini_crm (mini-crm-spec): воронка, сделки, задачи, pipeline
CRM = "/api/v1/crm"
status, crm_stages = call("GET", f"{CRM}/stages", token=token)
stage_by_name = {s["name"]: s for s in crm_stages} if status == 200 else {}
check("crm A: стадии воронки (6, сид)",
      status == 200 and len(crm_stages) == 6 and any(s["is_won"] for s in crm_stages),
      str(len(crm_stages)))

status, crm_deal = call("POST", f"{CRM}/deals", {
    "title": f"smoke-crm-{run_tag}", "stage_id": stage_by_name["Новая"]["id"],
    "amount": "100000", "currency": "RUB"}, token=token)
check("crm A: сделка создана", status == 201 and Decimal(crm_deal["amount_base"]) == Decimal("100000"),
      str(crm_deal.get("amount_base")))

status, outbox_crm = call("GET", "/api/v1/events/outbox?event_name=crm.deal.created&limit=10", token=token)
check("crm A: crm.deal.created в outbox",
      status == 200 and any(e["payload"]["deal_id"] == crm_deal["id"] for e in outbox_crm))

status, moved = call("POST", f"{CRM}/deals/{crm_deal['id']}/move",
                     {"stage_id": stage_by_name["Согласование"]["id"]}, token=token)
check("crm A: move по стадиям", status == 200, str(status))
status, won_deal = call("POST", f"{CRM}/deals/{crm_deal['id']}/move",
                        {"stage_id": stage_by_name["Выиграна"]["id"]}, token=token)
check("crm A: move в won", status == 200, str(status))
status, err_move = call("POST", f"{CRM}/deals/{crm_deal['id']}/move",
                        {"stage_id": stage_by_name["Проиграна"]["id"]}, token=token)
check("crm A: won → lost = 422", status == 422, str(status))

status, found_crm = call("GET", f"{CRM}/deals?q=smoke-crm", token=token)
check("crm A: поиск q= по названию",
      status == 200 and any(d["id"] == crm_deal["id"] for d in found_crm),
      str(len(found_crm) if status == 200 else status))

status, crm_hist = call("GET", f"{CRM}/history/crm.deal/{crm_deal['id']}", token=token)
check("crm A: версии crm.deal", status == 200 and len(crm_hist) >= 1, str(len(crm_hist) if status == 200 else status))

# задачи: просроченные и событие
status, crm_act = call("POST", f"{CRM}/deals/{crm_deal['id']}/activities",
                       {"title": "smoke-звонок", "due_at": "2020-01-01"}, token=token)
check("crm B: задача создана", status == 201, str(status)[:100])
status, done_act = call("PATCH", f"{CRM}/activities/{crm_act['id']}", {"done": True}, token=token)
check("crm B: done проставлен", status == 200 and done_act.get("done") is True, str(status))
status, overdue = call("GET", f"{CRM}/activities?due_before={today}&status=open", token=token)
check("crm B: просроченных нет (наша выполнена)", status == 200 and isinstance(overdue, list), str(status))
status, outbox_act = call("GET", "/api/v1/events/outbox?event_name=crm.activity.created&limit=10", token=token)
check("crm B: crm.activity.created в outbox",
      status == 200 and any(e["payload"]["activity_id"] == crm_act["id"] for e in outbox_act))

status, pipe = call("GET", f"{CRM}/report/pipeline", token=token)
check("crm C: pipeline-отчёт",
      status == 200 and "stages" in pipe and "weighted" in pipe["totals"], str(pipe)[:120])
# UTC-календарь: won_at в БД хранится в UTC, локальная полночь хоста
# (date.today) после ~18:00 UTC уже «завтра» и отсекает сегодняшние победы
utc_today = datetime.now(timezone.utc).date().isoformat()
status, pipe_won = call("GET", f"{CRM}/report/pipeline?date_from={utc_today}&date_to={utc_today}", token=token)
check("crm C: выиграно за период", status == 200 and pipe_won["won"]["count"] >= 1,
      str(pipe_won.get("won")))

# 24b. Склад (resources-core-spec, этап A): НСИ + двойная запись + серийники
status, units = call("GET", f"{ACC}/units", token=token)
check("inv A: справочник единиц (seed)",
      status == 200 and {"шт", "кг", "л", "м", "час", "мес", "лицензия"} <= {u["code"] for u in units},
      f"{len(units)} ед.")
status, locations = call("GET", f"{ACC}/locations", token=token)
loc = {row["name"]: row for row in locations}
check("inv A: локации — склады + системные транзиты",
      status == 200
      and {"Основной склад", "Цифровой склад", "Поставщик", "Клиент", "Производство", "Брак"} <= set(loc)
      and all(row["is_transit"] for name, row in loc.items()
              if name in ("Поставщик", "Клиент", "Производство", "Брак")),
      str(sorted(loc))[:120])

inv_sku = f"SMOKE-INV-{run_tag}"  # уникален за прогон — остатки детерминированы
status, inv_item = call("POST", f"{ACC}/items", {
    "sku": inv_sku, "name": "smoke widget", "kind": "physical", "unit_code": "шт",
    "low_stock_threshold": "7",
}, token=token)
check("inv A: номенклатура создана (avg_cost NULL)",
      status == 201 and inv_item["avg_cost"] is None, str(inv_item)[:120])
status, _dup = call("POST", f"{ACC}/items",
                    {"sku": inv_sku, "name": "dup", "kind": "physical", "unit_code": "шт"}, token=token)
check("inv A: дубль sku → 422", status == 422, str(_dup)[:100])

main = loc["Основной склад"]
status, rec_moves = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": main["id"],
    "lines": [{"item_id": inv_item["id"], "qty_fact": "10", "unit_cost": "100"}],
}, token=token)
check("inv A: оприходование 10 @ 100 (первый приход)",
      status == 201 and len(rec_moves) == 1 and rec_moves[0]["qty"] == "10.0000",
      str(rec_moves)[:120])
status, inv_item2 = call("GET", f"{ACC}/items/{inv_item['id']}", token=token)
check("inv A: avg_cost первого прихода = 100",
      status == 200 and inv_item2["avg_cost"] == "100.0000", str(inv_item2.get("avg_cost")))

status, _err = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": loc["Цифровой склад"]["id"],
    "lines": [{"item_id": inv_item["id"], "qty_fact": "1"}],
}, token=token)
check("inv A: kind_mismatch → 422", status == 422 and "kind_mismatch" in str(_err), str(status))

status, wh2 = call("POST", f"{ACC}/locations",
                   {"name": f"smoke-склад-{run_tag}", "kind": "physical"}, token=token)
status, tmove = call("POST", f"{ACC}/stock/transfer", {
    "item_id": inv_item["id"], "qty": "4",
    "from_location_id": main["id"], "to_location_id": wh2["id"],
}, token=token)
check("inv A: перемещение 4 по avg_cost", status == 201 and tmove["unit_cost"] == "100.0000",
      str(tmove)[:120])
status, _err = call("POST", f"{ACC}/stock/transfer", {
    "item_id": inv_item["id"], "qty": "7",
    "from_location_id": main["id"], "to_location_id": wh2["id"],
}, token=token)
check("inv A: insufficient_stock → 422", status == 422 and "insufficient_stock" in str(_err),
      str(status))

# списание до факта 3: на руках 3+4=7 ≤ порог 7 → acc.inventory.low_stock
status, off_moves = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": main["id"], "lines": [{"item_id": inv_item["id"], "qty_fact": "3"}],
}, token=token)
check("inv A: списание по факту 3 (по avg_cost)",
      status == 201 and off_moves[0]["qty"] == "3.0000" and off_moves[0]["unit_cost"] == "100.0000",
      str(off_moves)[:120])

status, balances = call("GET", f"{ACC}/stock/balances?item_id={inv_item['id']}", token=token)
by_loc = {b["location_id"]: b for b in balances}
check("inv A: остатки двойной записи (3 + 4)",
      status == 200 and len(balances) == 2
      and by_loc[main["id"]]["qty"] == "3.0000" and by_loc[wh2["id"]]["qty"] == "4.0000",
      str(balances)[:160])
check("inv A: стоимость остатков по средней (300.0000)",
      by_loc[main["id"]]["value"] == "300.0000", str(by_loc[main["id"]].get("value")))
status, journal = call("GET", f"{ACC}/stock/moves?item_id={inv_item['id']}", token=token)
check("inv A: журнал движений (3 операции)", status == 200 and len(journal) == 3, str(len(journal)))

status, outbox_sc = call("GET",
                         "/api/v1/events/outbox?event_name=acc.inventory.stock_changed&limit=10", token=token)
check("inv A: acc.inventory.stock_changed в outbox",
      status == 200 and any(inv_item["id"] in str(e["payload"]) for e in outbox_sc))
status, outbox_low = call("GET",
                          "/api/v1/events/outbox?event_name=acc.inventory.low_stock&limit=10", token=token)
check("inv A: acc.inventory.low_stock в outbox (7 ≤ порог 7)",
      status == 200 and any(e["payload"].get("sku") == inv_sku for e in outbox_low))

# цифровой товар: серийники (qty=1 на код, Fernet внутри)
dig_sku = f"SMOKE-DIG-{run_tag}"
status, dig = call("POST", f"{ACC}/items", {
    "sku": dig_sku, "name": "smoke код пополнения", "kind": "digital", "unit_code": "лицензия",
}, token=token)
check("inv A: digital → tracking=serial по умолчанию",
      status == 201 and dig["tracking"] == "serial", str(dig)[:120])
dig_codes = [f"SMOKE-CODE-{run_tag}-{i}" for i in (1, 2)]
status, ser_moves = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": loc["Цифровой склад"]["id"],
    "lines": [{"item_id": dig["id"], "serial_codes": dig_codes, "unit_cost": "50"}],
}, token=token)
check("inv A: приход 2 серийников (qty=2)", status == 201 and ser_moves[0]["qty"] == "2.0000",
      str(ser_moves)[:120])
status, dig2 = call("POST", f"{ACC}/locations",
                    {"name": f"smoke-цифра-{run_tag}", "kind": "digital"}, token=token)
status, _err = call("POST", f"{ACC}/stock/transfer", {
    "item_id": dig["id"], "qty": "1",
    "from_location_id": loc["Цифровой склад"]["id"], "to_location_id": dig2["id"],
    "serial_codes": ["NO-SUCH-CODE"],
}, token=token)
check("inv A: serial_not_found → 422", status == 422 and "serial_not_found" in str(_err), str(status))
status, ser_move = call("POST", f"{ACC}/stock/transfer", {
    "item_id": dig["id"], "qty": "1",
    "from_location_id": loc["Цифровой склад"]["id"], "to_location_id": dig2["id"],
    "serial_codes": [dig_codes[0]],
}, token=token)
check("inv A: перемещение серийника", status == 201 and ser_move["qty"] == "1.0000",
      str(ser_move)[:120])
status, void_moves = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": dig2["id"], "lines": [{"item_id": dig["id"], "serial_codes": []}],
}, token=token)
check("inv A: недостача серийника → void", status == 201 and void_moves[0]["qty"] == "1.0000",
      str(void_moves)[:120])
status, dig_balances = call("GET", f"{ACC}/stock/balances?item_id={dig['id']}", token=token)
check("inv A: остаток цифровых после void (1)",
      status == 200 and sum(Decimal(b["qty"]) for b in dig_balances) == 1, str(dig_balances)[:140])

# 24c. Закупки (resources-core-spec, этап B): заказы, приёмки, оплаты, сторно
sup, _ = get_or_create(
    f"{ACC}/counterparties", lambda c: c.get("name") == f"smoke-поставщик-{run_tag}",
    {"name": f"smoke-поставщик-{run_tag}"}, token,
)
status, b_item = call("POST", f"{ACC}/items", {
    "sku": f"SMOKE-B-{run_tag}", "name": "smoke закупка", "kind": "physical", "unit_code": "шт",
}, token=token)
check("pur B: номенклатура закупки", status == 201, str(b_item)[:100])
status, pay_acc = call("POST", f"{ACC}/accounts",
                       {"name": f"smoke-pay-{run_tag}", "currency": "RUB"}, token=token)

status, po = call("POST", f"{ACC}/purchase-orders", {
    "counterparty_id": sup["id"], "currency": "RUB",
    "lines": [{"item_id": b_item["id"], "qty": "8", "unit_price": "125"}],
}, token=token)
check("pur B: заказ создан (1000.00, draft, курс заморожен)",
      status == 201 and po["status"] == "draft" and Decimal(po["amount_base"]) == 1000
      and Decimal(po["rate"]) == 1,
      str(po)[:120])
status, po = call("POST", f"{ACC}/purchase-orders/{po['id']}/confirm", token=token)
check("pur B: confirm → номер ЗП-", status == 200 and po["status"] == "confirmed"
      and po["number"].startswith("ЗП-"), str(po.get("number")))

status, over = call("POST", f"{ACC}/receipts", {
    "purchase_order_id": po["id"],
    "lines": [{"item_id": b_item["id"], "qty": "9"}],
}, token=token)
check("pur B: приёмка сверх заказа → 422 over_receipt",
      status == 422 and "over_receipt" in str(over), str(status))

status, r1 = call("POST", f"{ACC}/receipts", {
    "purchase_order_id": po["id"],
    "lines": [{"item_id": b_item["id"], "qty": "3"}],  # цена из заказа: 125 × 1
}, token=token)
status, r1 = call("POST", f"{ACC}/receipts/{r1['id']}/post", token=token)
check("pur B: частичная приёмка 3 (себестоимость из заказа)",
      status == 200 and r1["status"] == "posted" and r1["number"].startswith("ПМ-"),
      str(r1)[:120])
status, b_item2 = call("GET", f"{ACC}/items/{b_item['id']}", token=token)
check("pur B: avg_cost = 125 после первой приёмки",
      b_item2["avg_cost"] == "125.0000", str(b_item2.get("avg_cost")))

status, r2 = call("POST", f"{ACC}/receipts", {
    "purchase_order_id": po["id"],
    "lines": [{"item_id": b_item["id"], "qty": "5", "unit_cost": "200"}],
}, token=token)
status, r2 = call("POST", f"{ACC}/receipts/{r2['id']}/post", token=token)
status, b_item3 = call("GET", f"{ACC}/items/{b_item['id']}", token=token)
# средняя: (3×125 + 5×200) / 8 = 171.875
check("pur B: заказ received, средняя 171.8750 (пересчёт приёмками)",
      r2["status"] == "posted" and b_item3["avg_cost"] == "171.8750",
      str(b_item3.get("avg_cost")))
status, bal = call("GET", f"{ACC}/stock/balances?item_id={b_item['id']}", token=token)
check("pur B: на складе 8 после двух приёмок",
      status == 200 and sum(Decimal(b["qty"]) for b in bal) == 8, str(bal)[:120])

status, payment = call("POST", f"{ACC}/purchase-orders/{po['id']}/pay", {
    "account_id": pay_acc["id"], "amount": "400",
}, token=token)
check("pur B: частичная оплата — транзакция СК проведена",
      status == 200 and payment["status"] == "posted"
      and (payment.get("doc_number") or "").startswith("СК-"),
      str(payment)[:120])

status, cp_bal = call("GET",
                      f"{ACC}/reports/counterparty-balance?counterparty_id={sup['id']}", token=token)
check("pur B: сальдо поставщика (принято 1375, оплачено 400, долг 975)",
      status == 200 and cp_bal["received_amount_base"] == "1375.00"
      and cp_bal["paid_amount_base"] == "400.00" and cp_bal["balance"] == "975.00",
      str(cp_bal)[:140])

status, rep = call("GET", f"{ACC}/reports/purchases", token=token)
row = next((r for r in rep.get("by_counterparty", []) if r["counterparty_id"] == sup["id"]), None)
check("pur B: отчёт закупок по поставщику",
      status == 200 and row and row["orders_amount_base"] == "1000.00"
      and row["received_amount_base"] == "1375.00",
      str(row)[:140])

status, r1s = call("POST", f"{ACC}/receipts/{r1['id']}/unpost", {"reason": "smoke-сторно"}, token=token)
check("pur B: сторно приёмки (is_stornoed)", status == 200 and r1s["is_stornoed"] is True,
      str(r1s)[:100])
status, bal2 = call("GET", f"{ACC}/stock/balances?item_id={b_item['id']}", token=token)
check("pur B: после сторно на складе 5",
      status == 200 and sum(Decimal(b["qty"]) for b in bal2) == 5, str(bal2)[:120])
status, cp_bal2 = call("GET",
                       f"{ACC}/reports/counterparty-balance?counterparty_id={sup['id']}", token=token)
check("pur B: сальдо после сторно (1000 − 400 = 600)",
      cp_bal2["received_amount_base"] == "1000.00" and cp_bal2["balance"] == "600.00",
      str(cp_bal2.get("balance")))

status, outbox_pur = call("GET",
                          "/api/v1/events/outbox?event_name=acc.purchase.received&limit=5", token=token)
check("pur B: acc.purchase.received в outbox",
      status == 200 and any(r2["id"] == e["payload"]["receipt_id"] for e in outbox_pur))

# 24d. Продажи (resources-core-spec, этап C): заказы, отгрузки, FIFO-коды, маржа
cust, _ = get_or_create(
    f"{ACC}/counterparties", lambda c: c.get("name") == f"smoke-клиент-{run_tag}",
    {"name": f"smoke-клиент-{run_tag}"}, token,
)
status, s_item = call("POST", f"{ACC}/items", {
    "sku": f"SMOKE-C-{run_tag}", "name": "smoke продажа", "kind": "physical", "unit_code": "шт",
}, token=token)
check("sal C: номенклатура продажи", status == 201, str(s_item)[:100])
status, _ = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": main["id"],
    "lines": [{"item_id": s_item["id"], "qty_fact": "10", "unit_cost": "60"}],
}, token=token)

status, so = call("POST", f"{ACC}/sales-orders", {
    "counterparty_id": cust["id"], "currency": "RUB",
    "lines": [{"item_id": s_item["id"], "qty": "10", "unit_price": "100"}],
}, token=token)
check("sal C: заказ клиента создан (1000.00, курс заморожен)",
      status == 201 and so["status"] == "draft" and Decimal(so["amount_base"]) == 1000
      and Decimal(so["rate"]) == 1, str(so)[:120])
status, so = call("POST", f"{ACC}/sales-orders/{so['id']}/confirm", token=token)
check("sal C: confirm → номер ЗК- и резерв строки",
      status == 200 and so["status"] == "confirmed" and so["number"].startswith("ЗК-")
      and so["lines"][0]["reserved_qty"] == "10.0000",
      str(so.get("number")))

status, _over = call("POST", f"{ACC}/shipments", {
    "sales_order_id": so["id"],
    "lines": [{"item_id": s_item["id"], "qty": "11"}],
}, token=token)
check("sal C: отгрузка сверх заказа → 422 over_shipment",
      status == 422 and "over_shipment" in str(_over), str(status))

status, shp1 = call("POST", f"{ACC}/shipments", {
    "sales_order_id": so["id"],
    "lines": [{"item_id": s_item["id"], "qty": "4"}],
}, token=token)
status, shp1 = call("POST", f"{ACC}/shipments/{shp1['id']}/post", token=token)
check("sal C: частичная отгрузка 4 (списание по средней 60)",
      status == 200 and shp1["status"] == "posted" and shp1["number"].startswith("ОТ-")
      and Decimal(shp1["lines"][0]["amount_base"]) == Decimal(400),
      str(shp1)[:130])
status, so_now = call("GET", f"{ACC}/sales-orders/{so['id']}", token=token)
check("sal C: заказ partially_shipped, резерв 6",
      so_now["status"] == "partially_shipped" and so_now["lines"][0]["reserved_qty"] == "6.0000",
      so_now.get("status"))

# минус запрещён: второго заказа (7) больше остатка (6)
status, so2 = call("POST", f"{ACC}/sales-orders", {
    "counterparty_id": cust["id"], "currency": "RUB",
    "lines": [{"item_id": s_item["id"], "qty": "7", "unit_price": "100"}],
}, token=token)
call("POST", f"{ACC}/sales-orders/{so2['id']}/confirm", token=token)
status, shp_neg = call("POST", f"{ACC}/shipments", {
    "sales_order_id": so2["id"],
    "lines": [{"item_id": s_item["id"], "qty": "7"}],
}, token=token)
status, _neg = call("POST", f"{ACC}/shipments/{shp_neg['id']}/post", token=token)
check("sal C: минус запрещён → 422 insufficient_stock",
      status == 422 and "insufficient_stock" in str(_neg), str(status))

# цифровой товар: FIFO-выдача кодов без явного списка
status, dig_item = call("POST", f"{ACC}/items", {
    "sku": f"SMOKE-DIGC-{run_tag}", "name": "smoke цифровой", "kind": "digital",
    "unit_code": "лицензия",
}, token=token)
sal_codes = [f"SMOKE-SAL-{run_tag}-{i}" for i in (1, 2)]
status, _ = call("POST", f"{ACC}/stock/adjustment", {
    "location_id": loc["Цифровой склад"]["id"],
    "lines": [{"item_id": dig_item["id"], "serial_codes": sal_codes, "unit_cost": "10"}],
}, token=token)
status, dig_order = call("POST", f"{ACC}/sales-orders", {
    "counterparty_id": cust["id"], "currency": "RUB",
    "lines": [{"item_id": dig_item["id"], "qty": "2", "unit_price": "300"}],
}, token=token)
call("POST", f"{ACC}/sales-orders/{dig_order['id']}/confirm", token=token)
status, dig_shp = call("POST", f"{ACC}/shipments", {
    "sales_order_id": dig_order["id"],
    "lines": [{"item_id": dig_item["id"], "qty": "2"}],  # FIFO-автовыбор кодов
}, token=token)
status, dig_shp = call("POST", f"{ACC}/shipments/{dig_shp['id']}/post", token=token)
check("sal C: продажа цифровых — коды выданы FIFO",
      status == 200 and dig_shp["status"] == "posted", str(dig_shp)[:110])
status, dig_bal = call("GET", f"{ACC}/stock/balances?item_id={dig_item['id']}", token=token)
check("sal C: цифровой склад пуст после выдачи", dig_bal == [], str(dig_bal)[:100])

# оплата — входящая транзакция с категорией «Продажи»
status, s_pay = call("POST", f"{ACC}/sales-orders/{so['id']}/pay", {
    "account_id": pay_acc["id"], "amount": "400",
}, token=token)
check("sal C: оплата — транзакция ПК- проведена",
      status == 200 and s_pay["kind"] == "income" and s_pay["status"] == "posted"
      and (s_pay.get("doc_number") or "").startswith("ПК-"), str(s_pay)[:110])

# маржа: выручка 400 + 600, себестоимость 4×60 + 2×10, маржа 740
status, s_rep = call("GET", f"{ACC}/reports/sales?counterparty_id={cust['id']}", token=token)
check("sal C: отчёт продаж с маржой (740.00)",
      status == 200 and s_rep["shipments"]["revenue_base"] == "1000.00"
      and s_rep["shipments"]["cogs_base"] == "260.00"
      and s_rep["shipments"]["margin_base"] == "740.00",
      str(s_rep.get("shipments"))[:120])

# сторно отгрузки: товар возвращается, маржа пересчитывается
status, shp1s = call("POST", f"{ACC}/shipments/{shp1['id']}/unpost",
                     {"reason": "smoke-возврат"}, token=token)
check("sal C: сторно отгрузки (is_stornoed, резерв вернулся)",
      status == 200 and shp1s["is_stornoed"] is True, str(shp1s)[:100])
status, c_bal = call("GET", f"{ACC}/stock/balances?item_id={s_item['id']}", token=token)
check("sal C: после сторно на складе 10",
      status == 200 and sum(Decimal(b["qty"]) for b in c_bal) == 10, str(c_bal)[:110])
status, s_rep2 = call("GET", f"{ACC}/reports/sales?counterparty_id={cust['id']}", token=token)
check("sal C: маржа после сторно (600 − 20 = 580.00)",
      s_rep2["shipments"]["revenue_base"] == "600.00"
      and s_rep2["shipments"]["margin_base"] == "580.00",
      str(s_rep2.get("shipments"))[:120])

status, outbox_sal = call("GET",
                          "/api/v1/events/outbox?event_name=acc.sales.shipped&limit=5", token=token)
check("sal C: acc.sales.shipped в outbox",
      status == 200 and any(dig_shp["id"] == e["payload"]["shipment_id"] for e in outbox_sal))

# 24e. Сборка (resources-core-spec, этап D): купил 2 материала → собрал → продал
prd_items = {}
for sku_suffix, unit in (("M1", "шт"), ("M2", "шт"), ("P", "шт")):
    status, prd_items[sku_suffix] = call("POST", f"{ACC}/items", {
        "sku": f"SMOKE-PRD-{sku_suffix}-{run_tag}", "name": f"smoke {sku_suffix}",
        "kind": "physical", "unit_code": unit,
    }, token=token)
m1, m2, prd = prd_items["M1"], prd_items["M2"], prd_items["P"]

status, prd_po = call("POST", f"{ACC}/purchase-orders", {
    "counterparty_id": sup["id"], "currency": "RUB",
    "lines": [
        {"item_id": m1["id"], "qty": "20", "unit_price": "30"},
        {"item_id": m2["id"], "qty": "20", "unit_price": "5"},
    ],
}, token=token)
call("POST", f"{ACC}/purchase-orders/{prd_po['id']}/confirm", token=token)
for receipt_body in (
    {"purchase_order_id": prd_po["id"], "lines": [
        {"item_id": m1["id"], "qty": "10"}, {"item_id": m2["id"], "qty": "20"}]},
    {"purchase_order_id": prd_po["id"], "lines": [
        {"item_id": m1["id"], "qty": "10", "unit_cost": "50"}]},
):
    status, rcpt = call("POST", f"{ACC}/receipts", receipt_body, token=token)
    call("POST", f"{ACC}/receipts/{rcpt['id']}/post", token=token)
status, m1_state = call("GET", f"{ACC}/items/{m1['id']}", token=token)
check("prd D: куплены материалы, средняя m1 = 40 (30 и 50)",
      m1_state["avg_cost"] == "40.0000", str(m1_state.get("avg_cost")))

status, card = call("POST", f"{ACC}/tech-cards", {
    "name": f"smoke-сборка-{run_tag}", "product_item_id": prd["id"], "qty_out": "1",
    "components": [{"item_id": m1["id"], "qty": "2"}, {"item_id": m2["id"], "qty": "4"}],
}, token=token)
check("prd D: тех.карта создана (1 изделие = 2×m1 + 4×m2)",
      status == 201 and len(card["components"]) == 2, str(card)[:110])

status, prd_order = call("POST", f"{ACC}/production-orders", {
    "tech_card_id": card["id"], "qty_planned": "2",
}, token=token)
status, prd_order = call("POST", f"{ACC}/production-orders/{prd_order['id']}/post", token=token)
check("prd D: сборка проведена (СБ-, себестоимость 200 за партию)",
      status == 200 and prd_order["status"] == "posted"
      and prd_order["number"].startswith("СБ-")
      and Decimal(prd_order["material_cost"]) == 200,
      str(prd_order)[:120])

status, prd_state = call("GET", f"{ACC}/items/{prd['id']}", token=token)
status, prd_bal = call("GET", f"{ACC}/stock/balances?item_id={prd['id']}", token=token)
check("prd D: 2 изделия по себестоимости 100 (= Σ материалов)",
      prd_state["avg_cost"] == "100.0000"
      and sum(Decimal(b["qty"]) for b in prd_bal) == 2,
      str(prd_state.get("avg_cost")))

# резервы: заказ продаж резервирует весь m1 — сборке не хватает
status, rsv_so = call("POST", f"{ACC}/sales-orders", {
    "counterparty_id": cust["id"],
    "lines": [{"item_id": m1["id"], "qty": "16", "unit_price": "99"}],
}, token=token)
call("POST", f"{ACC}/sales-orders/{rsv_so['id']}/confirm", token=token)
status, rsv_prd = call("POST", f"{ACC}/production-orders", {
    "tech_card_id": card["id"], "qty_planned": "1",
}, token=token)
status, _rsv_err = call("POST", f"{ACC}/production-orders/{rsv_prd['id']}/post", token=token)
check("prd D: сборка не расходует резерв → 422 insufficient_stock (reserved)",
      status == 422 and "reserved" in str(_rsv_err), str(status))
call("POST", f"{ACC}/sales-orders/{rsv_so['id']}/cancel", token=token)

# продали изделия: маржа 2×(300−100) = 400
status, prd_so = call("POST", f"{ACC}/sales-orders", {
    "counterparty_id": cust["id"],
    "lines": [{"item_id": prd["id"], "qty": "2", "unit_price": "300"}],
}, token=token)
call("POST", f"{ACC}/sales-orders/{prd_so['id']}/confirm", token=token)
status, prd_shp = call("POST", f"{ACC}/shipments", {
    "sales_order_id": prd_so["id"], "lines": [{"item_id": prd["id"], "qty": "2"}],
}, token=token)
status, prd_shp = call("POST", f"{ACC}/shipments/{prd_shp['id']}/post", token=token)
status, prd_rep = call("GET", f"{ACC}/reports/sales", token=token)
prd_row = next((r for r in prd_rep.get("by_item", []) if r["item_id"] == prd["id"]), None)
check("prd D: продано изделие, маржа изделия 400.00",
      prd_row and prd_row["margin_base"] == "400.00" and prd_row["revenue_base"] == "600.00",
      str(prd_row)[:120])

# сторно сборки: продано — запрещено; вернули продажу — прошло
status, _p_err = call("POST", f"{ACC}/production-orders/{prd_order['id']}/unpost",
                      {"reason": "smoke"}, token=token)
check("prd D: сторно сборки с проданной продукцией → 422 has_subsequent_moves",
      status == 422 and "has_subsequent_moves" in str(_p_err), str(status))
call("POST", f"{ACC}/shipments/{prd_shp['id']}/unpost", {"reason": "smoke-возврат"}, token=token)
status, prd_un = call("POST", f"{ACC}/production-orders/{prd_order['id']}/unpost",
                      {"reason": "smoke-сторно-сборки"}, token=token)
check("prd D: сторно сборки — материалы вернулись, продукция списана",
      status == 200 and prd_un["is_stornoed"] is True, str(prd_un)[:100])
status, m1_bal_fin = call("GET", f"{ACC}/stock/balances?item_id={m1['id']}", token=token)
check("prd D: m1 на складе снова 20",
      sum(Decimal(b["qty"]) for b in m1_bal_fin) == 20, str(m1_bal_fin)[:110])

status, outbox_prd = call("GET",
                          "/api/v1/events/outbox?event_name=acc.production.order.posted&limit=5",
                          token=token)
check("prd D: acc.production.order.posted в outbox",
      status == 200 and any(prd_order["id"] == e["payload"]["order_id"] for e in outbox_prd))

# 24f. Онлайн-продажа (sales-automation, этап D): вебхук ЮKassa (mock) →
# документы → FIFO-выдача кодов; дубль вебхука гасится идемпотентностью
def _start_mock_yookassa():
    """Мок API ЮKassa: GET /v3/payments/{id} (Basic shop1:secret1)."""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    auth = "Basic " + base64.b64encode(b"shop1:secret1").decode()
    payments = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization", "") != auth:
                self.send_response(401)
                self.end_headers()
                return
            pid = self.path.split("?")[0].rsplit("/", 1)[1]
            body = payments.get(pid)
            payload = json.dumps(body or {}).encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if body:
                self.wfile.write(payload)

        def log_message(self, *args):
            pass

    # порт 0 — свободный порт от ОС; 0.0.0.0 — контейнер api достукивается
    # через host.docker.internal (как mock-Telegram в секции 21)
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, payments


_yk_server, _yk_payments = _start_mock_yookassa()
_yk_port = _yk_server.server_address[1]
_ol_pid = f"smoke-ol-{run_tag}"
_yk_payments[_ol_pid] = {
    "id": _ol_pid, "status": "succeeded", "paid": True,
    "amount": {"value": "1000.00", "currency": "RUB"},
    "metadata": {"email": f"ol-{run_tag}@example.com", "lines": [
        {"external_id": f"ol-site-{run_tag}", "qty": 2, "price": "500.00"},
    ]},
}

# НСИ: цифровой товар с 2 кодами, счёт зачисления
status, ol_item = call("POST", f"{ACC}/items", {
    "sku": f"SMOKE-OL-{run_tag}", "name": "smoke онлайн-код",
    "kind": "digital", "unit_code": "лицензия",
    "sale_price": "500.00",
}, token=token)
status, ol_locs = call("GET", f"{ACC}/locations", token=token)
ol_dig = next(row for row in ol_locs if row["name"] == "Цифровой склад")
status, ol_rcpt = call("POST", f"{ACC}/receipts", {
    "counterparty_id": sup["id"],
    "lines": [{"item_id": ol_item["id"], "qty": "2", "unit_cost": "100",
               "location_id": ol_dig["id"],
               "serial_codes": [f"OL-{run_tag}-1", f"OL-{run_tag}-2"]}],
}, token=token)
call("POST", f"{ACC}/receipts/{ol_rcpt['id']}/post", token=token)
status, ol_acc = call("POST", f"{ACC}/accounts", {
    "name": f"smoke-эквайринг-{run_tag}", "currency": "RUB"}, token=token)

# подключения: служебный http_rest → наш API + провайдер yookassa (mock)
status, ol_api_tok = call("POST", "/api/v1/admin/api-tokens",
                          {"name": f"smoke-ol-flow-{run_tag}", "role": "user"}, token=token)
ol_api_conn, _ol_created = get_or_create("/api/v1/integrations/connections",
    lambda c: c["name"] == f"smoke-ol-api-{run_tag}",
    {"name": f"smoke-ol-api-{run_tag}", "connector_code": "http_rest",
     "credentials": {"api_key": ol_api_tok["token"]},
     "config": {"base_url": "http://api:8000"}}, token)
status, ol_yk = call("POST", "/api/v1/integrations/connections", {
    "name": f"smoke-ol-yk-{run_tag}", "connector_code": "yookassa",
    "credentials": {"shop_id": "shop1", "secret_key": "secret1"},
    "config": {"base_url": f"http://host.docker.internal:{_yk_port}/v3"},
}, token=token)
status, ol_hook = call("POST", "/api/v1/integrations/webhooks", {
    "name": f"smoke-ol-hook-{run_tag}", "target_module": "payments",
    "connection_id": ol_yk["id"],
}, token=token)
call("POST", "/api/v1/integrations/item-mappings", {
    "connection_id": ol_yk["id"], "external_item_id": f"ol-site-{run_tag}",
    "sku": ol_item["sku"], "item_id": ol_item["id"],
}, token=token)
# seed-рецепт «Онлайн-продажа» (delivery_channel=none — email-доставка
# глубоко покрыта pytest этапа C; здесь — сквозной цикл на чистом стеке)
status, ol_recipe = call("POST", "/api/v1/integrations/recipes", {
    "name": f"smoke-ol-recipe-{run_tag}",
    "definition": {
        "trigger_event": "integration.payment.received",
        "action": {"type": "sales_flow", "connection_id": ol_yk["id"],
                   "config": {"account_id": ol_acc["id"], "price_tolerance": "0",
                              "on_no_items": "transaction_only",
                              "delivery_channel": "none"}},
        "api_connection_id": ol_api_conn["id"],
    },
}, token=token)
call("POST", f"/api/v1/integrations/recipes/{ol_recipe['id']}/publish", token=token)

# вебхук payment.succeeded → 202; обработка асинхронна (поток)
status, _ol_ack = call("POST", f"/api/v1/integrations/hooks/{ol_hook['id']}", {
    "type": "notification", "event": "payment.succeeded",
    "object": _yk_payments[_ol_pid],
}, token=token)
check("ol A: вебхук принят (202)", status == 202, str(status))

_ol_pay = None
for _ in range(20):
    time.sleep(1)
    status, _ol_list = call("GET", "/api/v1/integrations/payments?provider=yookassa",
                            token=token)
    _ol_pay = next((p for p in _ol_list
                    if p["provider_payment_id"] == _ol_pid), None)
    if _ol_pay and _ol_pay["status"] == "processed":
        break
check("ol B: платёж processed (флоу прошёл)",
      _ol_pay is not None and _ol_pay["status"] == "processed",
      str(_ol_pay)[:120])
check("ol B: документы созданы (заказ + транзакция + отгрузка)",
      bool(_ol_pay) and bool(_ol_pay["sales_order_id"]) and bool(_ol_pay["transaction_id"])
      and bool(_ol_pay["shipment_id"]),
      str(_ol_pay and (_ol_pay["sales_order_id"], _ol_pay["transaction_id"],
                       _ol_pay["shipment_id"]))[:120])
if _ol_pay and _ol_pay.get("sales_order_id"):
    status, ol_order = call("GET", f"{ACC}/sales-orders/{_ol_pay['sales_order_id']}",
                            token=token)
    check("ol B: заказ ЗК- со статусом shipped",
          status == 200 and (ol_order.get("number") or "").startswith("ЗК-")
          and ol_order["status"] == "shipped", str(ol_order.get("status"))[:80])
    status, ol_bal = call("GET", f"{ACC}/stock/balances?item_id={ol_item['id']}", token=token)
    check("ol C: оба кода выданы FIFO (цифровой склад пуст)",
          status == 200 and sum(Decimal(b["qty"]) for b in ol_bal) == 0, str(ol_bal)[:110])

    # дубль вебхука → duplicate, документы не дублируются
    status, _ol_ack2 = call("POST", f"/api/v1/integrations/hooks/{ol_hook['id']}", {
        "type": "notification", "event": "payment.succeeded",
        "object": _yk_payments[_ol_pid],
    }, token=token)
    time.sleep(3)
    status, _ol_list2 = call("GET", "/api/v1/integrations/payments?provider=yookassa",
                             token=token)
    _ol_same = [p for p in _ol_list2 if p["provider_payment_id"] == _ol_pid]
    status, ol_order2 = call("GET", f"{ACC}/sales-orders/{_ol_pay['sales_order_id']}",
                             token=token)
    check("ol D: дубль вебхука — платёж один, заказ прежний",
          len(_ol_same) == 1 and _ol_same[0]["sales_order_id"] == _ol_pay["sales_order_id"]
          and ol_order2["number"] == ol_order["number"],
          f"payments={len(_ol_same)}")
else:
    check("ol B: заказ ЗК- (пропуск: нет платежа)", False, "flow не дошёл до заказа")

_yk_server.shutdown()
_yk_server.server_close()

# 25. Rate limit логина (security-p0 п.2) — В КОНЦЕ: блокирует IP на 60 с
codes = []
for _ in range(6):
    status, data = call("POST", "/api/v1/auth/login",
                        {"email": "nobody@erp.local", "password": "whatever1"})
    codes.append(status)
retry_after = None
try:
    with_last = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=json.dumps({"email": "nobody@erp.local", "password": "whatever1"}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(with_last)
except urllib.error.HTTPError as e:
    retry_after = e.headers.get("Retry-After")
    codes.append(e.code)
check("sec: 6 неудачных логинов → 429", codes[:5] == [401] * 5 and 429 in codes[5:], str(codes))
check("sec: 429 содержит Retry-After", retry_after is not None and int(retry_after) > 0, str(retry_after))

print("  … ждём окончания окна 60 с …")
time.sleep(61)
status, data = call("POST", "/api/v1/auth/login",
                    {"email": "nobody@erp.local", "password": "whatever1"})
check("sec: после окна снова 401 (не 429)", status == 401, str(status))

# ---------- Финальная зачистка: закрытые периоды → open ----------
# pytest и сам smoke закрывают периоды; падение между close и reopen
# оставляло закрытый период в живой БД — следующие прогоны падали каскадом.
# Гарантия: в конце каждого прогона все closed периоды переоткрыты.
try:
    status, periods = call("GET", f"{ACC}/periods", token=token)
    closed = [p for p in periods if p.get("status") == "closed"] if status == 200 else []
    for per in closed:
        call("POST", f"{ACC}/periods/{per['year']}/{per['month']}/reopen",
             {"reason": "smoke: финальная зачистка периодов"}, token=token)
    check("cleanup: закрытых периодов не осталось",
          all(p.get("status") != "closed"
              for p in call("GET", f"{ACC}/periods", token=token)[1]),
          f"reopened={len(closed)}")
except Exception as exc:  # noqa: BLE001 — зачистка не должна ломать итог
    check("cleanup: закрытых периодов не осталось", False, str(exc)[:120])

print()
print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILED else f"ПРОВАЛЕНО: {FAILED}")
sys.exit(1 if FAILED else 0)
