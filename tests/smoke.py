"""Smoke-тест работающей системы (только stdlib): python tests/smoke.py [base_url]"""

import json
import sys
import urllib.error
import urllib.request

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


# 1. Health + модули
status, data = call("GET", "/health")
check("GET /health", status == 200 and data.get("status") == "ok", str(data))

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

# 4. Подключение + проверка связи (self-loop на сам API)
status, conn = call("POST", "/api/v1/integrations/connections", {
    "name": "smoke-test",
    "connector_code": "http_rest",
    "credentials": {"api_key": "dummy"},
    "config": {"base_url": "http://api:8000", "health_path": "/health", "auth_style": "none"},
}, token=token)
check("POST /api/v1/integrations/connections", status == 201 and "id" in conn, str(conn)[:120])

if "id" in conn:
    status, data = call("POST", f"/api/v1/integrations/connections/{conn['id']}/test", token=token)
    check("POST /connections/{id}/test", status == 200 and data.get("ok") is True, str(data)[:120])

    # 5. Sync job: fetch /health через worker
    status, job = call("POST", "/api/v1/integrations/sync-jobs", {
        "name": "smoke-fetch", "connection_id": conn["id"],
        "direction": "fetch", "endpoint": "/health",
    }, token=token)
    check("POST /api/v1/integrations/sync-jobs", status == 201 and "id" in job, str(job)[:120])
    if "id" in job:
        status, data = call("POST", f"/api/v1/integrations/sync-jobs/{job['id']}/run", token=token)
        check("POST /sync-jobs/{id}/run (queued)", status == 200 and data.get("queued"), str(data)[:120])

# 6. Webhook: создание, неверный токен, верный токен
status, hook = call("POST", "/api/v1/integrations/webhooks", {"name": "smoke-hook"}, token=token)
check("POST /api/v1/integrations/webhooks", status == 201 and "url_path" in hook, str(hook)[:160])

if "url_path" in hook:
    status, data = call("POST", hook["url_path"], {"event": "ping"}, headers={"X-ERP-Token": "wrong"})
    check("webhook отклоняет неверный токен", status == 401, str(status))
    status, data = call("POST", hook["url_path"], {"event": "ping"}, headers={"X-ERP-Token": hook["secret_token"]})
    check("webhook принимает верный токен", status == 202, str(status))

print()
print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILED else f"ПРОВАЛЕНО: {FAILED}")
sys.exit(1 if FAILED else 0)
