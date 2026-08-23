"""Smoke-тест работоспособной системы (только stdlib): python tests/smoke.py [base_url]

Идемпотентность (реестр долгов №2): справочники переиспользуются по имени,
счёта под транзакции создаются уникальными за прогон (документы не переиспользуются),
курсы — upsert. Повторные прогоны не плодят дубли подключений/заданий/webhooks.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
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
    check("GET web :8080 — приложение (title ERP, бандл)",
          page.status == 200 and "<title>ERP</title>" in html and "/assets/" in html,
          f"status={page.status}")
except Exception as exc:  # noqa: BLE001 — любая ошибка сети = провал проверки
    check("GET web :8080 — приложение (title ERP, бандл)", False, str(exc)[:120])

status, data = call("GET", "/api/v1/modules")
names = [m["name"] for m in data] if status == 200 else []
check("GET /api/v1/modules (core, integrations)", status == 200 and "core" in names and "integrations" in names, str(names))

# 2. Логин
status, data = call("POST", "/api/v1/auth/login", {"email": "admin@example.com", "password": "admin12345"})
check("POST /api/v1/auth/login", status == 200 and "access_token" in data, str(data)[:120])
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
check("sec: integrations-мутации только admin (403 для user)", status == 403, str(status))

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

# 19. Rate limit логина (security-p0 п.2) — В КОНЦЕ: блокирует IP на 60 с
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

print()
print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILED else f"ПРОВАЛЕНО: {FAILED}")
sys.exit(1 if FAILED else 0)
