"""Роли и права (редизайн §6): матрица role_permissions, /me/permissions,
CRUD ролей, require_module — 403/200 по уровням доступа.

Интеграционные тесты против запущенного API (docker compose up).
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration

READONLY_EMAIL = "readonly.roles-test@erp.local"
READONLY_PASSWORD = "readonly1pass"

MODULES = ("accounting", "crm", "integrations", "ai", "system")
# инструменты devtools §5: admin — rw по ветке кода; встроенным — none
TOOL_MODULES = ("table_browser", "maint_views", "devtools")

# PUT принимает всю матрицу разом (спека §6.2) — хелпер полной матрицы readonly
def _ro(**overrides):
    matrix = dict.fromkeys(MODULES, "ro")
    matrix.update(overrides)
    return {"permissions": matrix}



@pytest.fixture(scope="module")
def client():
    http = httpx.Client(base_url=BASE_URL, timeout=10)
    try:
        http.get("/health").raise_for_status()
    except httpx.HTTPError:
        http.close()
        pytest.skip("API not running (docker compose up)")
    yield http
    http.close()


def _login(http: httpx.Client, email: str, password: str) -> dict:
    response = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    data = response.json()
    # мультитенантность: супер-админу для работы с данными нужна организация
    orgs = data.get("organizations") or []
    if orgs:
        org_id = next((o["id"] for o in orgs if o.get("name") == "Основная"),
                      orgs[0]["id"])
        data = http.post("/api/v1/auth/select-org", json={
            "refresh_token": data["refresh_token"], "company_id": org_id}).json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, "admin@example.com", os.environ.get("ERP_ADMIN_PASSWORD", "admin12345"))


@pytest.fixture(scope="module")
def readonly_headers(client, admin_headers):
    client.post("/api/v1/users", json={
        "email": READONLY_EMAIL, "password": READONLY_PASSWORD, "role": "readonly",
    }, headers=admin_headers)  # 409 на дубль между прогонами — не важно
    return _login(client, READONLY_EMAIL, READONLY_PASSWORD)


# ---------- /me/permissions ----------

def test_me_permissions_admin(client, admin_headers):
    response = client.get("/api/v1/me/permissions", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["role"]["key"] == "admin"
    assert data["permissions"] == {**dict.fromkeys(MODULES, "rw"),
                                   **dict.fromkeys(TOOL_MODULES, "rw")}


def test_me_permissions_readonly(client, readonly_headers):
    data = client.get("/api/v1/me/permissions", headers=readonly_headers).json()
    assert data["role"]["key"] == "readonly"
    assert data["permissions"] == {**dict.fromkeys(MODULES, "ro"),
                                   **dict.fromkeys(TOOL_MODULES, "none")}


def test_me_permissions_requires_auth(client):
    assert client.get("/api/v1/me/permissions").status_code == 401


# ---------- Список ролей ----------

def test_roles_list_admin_only(client, admin_headers, readonly_headers):
    assert client.get("/api/v1/roles", headers=readonly_headers).status_code == 403
    response = client.get("/api/v1/roles", headers=admin_headers)
    assert response.status_code == 200
    roles = {r["key"]: r for r in response.json()}
    assert set(roles) >= {"admin", "user", "readonly"}
    admin_role = roles["admin"]
    assert admin_role["is_builtin"] is True
    assert admin_role["permissions"] == {**dict.fromkeys(MODULES, "rw"),
                                          **dict.fromkeys(TOOL_MODULES, "rw")}
    assert roles["user"]["permissions"] == {
        "accounting": "rw", "crm": "rw", "integrations": "rw", "ai": "rw", "system": "ro",
        "table_browser": "none", "maint_views": "none", "devtools": "none",
    }
    assert roles["readonly"]["permissions"] == {**dict.fromkeys(MODULES, "ro"),
                                                 **dict.fromkeys(TOOL_MODULES, "none")}
    assert admin_role["users_count"] >= 1


# ---------- Матрица прав: PUT ----------

def test_put_permissions_admin_role_forbidden(client, admin_headers):
    response = client.put("/api/v1/roles/admin/permissions", json={
        "permissions": dict.fromkeys(MODULES + TOOL_MODULES, "none"),
    }, headers=admin_headers)
    assert response.status_code == 400


def test_put_permissions_validation(client, admin_headers):
    bad_module = client.put("/api/v1/roles/readonly/permissions", json={
        "permissions": {"nope": "rw"},
    }, headers=admin_headers)
    assert bad_module.status_code == 422
    bad_level = client.put("/api/v1/roles/readonly/permissions", json={
        "permissions": {"accounting": "admin"},
    }, headers=admin_headers)
    assert bad_level.status_code == 422
    assert client.put("/api/v1/roles/missing/permissions", json={
        "permissions": {},
    }, headers=admin_headers).status_code == 404


def test_put_permissions_applies_immediately(client, admin_headers, readonly_headers):
    """Смена прав применяется без перелогина (спека §6.3): readonly получает
    none на integrations → 403; возврат ro → снова 200."""
    assert client.get("/api/v1/integrations/connections",
                      headers=readonly_headers).status_code == 200
    closed = client.put("/api/v1/roles/readonly/permissions", json=_ro(integrations="none"),
                        headers=admin_headers)
    assert closed.status_code == 200
    assert closed.json()["permissions"]["integrations"] == "none"
    assert client.get("/api/v1/integrations/connections",
                      headers=readonly_headers).status_code == 403
    restored = client.put("/api/v1/roles/readonly/permissions", json=_ro(),
                          headers=admin_headers)
    assert restored.status_code == 200
    assert client.get("/api/v1/integrations/connections",
                      headers=readonly_headers).status_code == 200


# ---------- require_module: 403/200 по уровням ----------

@pytest.mark.parametrize("method,path,body", [
    # integrations: чтение ro, мутация rw
    ("GET", "/api/v1/integrations/connections", None),
    ("POST", "/api/v1/integrations/connections",
     {"name": "x", "connector_code": "http", "credentials": {}, "config": {}}),
    # ai: чтение ro, мутация rw
    ("GET", "/api/v1/ai/documents", None),
    ("GET", "/api/v1/ai/sessions", None),
])
def test_readonly_read_only_modules(client, readonly_headers, method, path, body):
    """readonly (ro везде): GET-модульные эндпоинты — 200, мутации — 403."""
    response = client.request(method, path, json=body, headers=readonly_headers)
    assert response.status_code == 200 if method == "GET" else 403, \
        f"{method} {path} -> {response.status_code}"


def test_role_without_module_line_is_forbidden(client, admin_headers, readonly_headers):
    """Отсутствие строки прав = none: закрытый модуль недоступен даже на чтение."""
    client.put("/api/v1/roles/readonly/permissions", json=_ro(ai="none"),
               headers=admin_headers)
    try:
        assert client.get("/api/v1/ai/documents", headers=readonly_headers).status_code == 403
        assert client.get("/api/v1/ai/sessions", headers=readonly_headers).status_code == 403
    finally:
        client.put("/api/v1/roles/readonly/permissions", json=_ro(),
                   headers=admin_headers)


def test_admin_full_access_to_modules(client, admin_headers):
    assert client.get("/api/v1/integrations/connections",
                      headers=admin_headers).status_code == 200
    assert client.get("/api/v1/ai/documents", headers=admin_headers).status_code == 200


# ---------- Кастомные роли ----------

def test_custom_role_crud(client, admin_headers):
    # зачистка от прошлого прогона: пользователь тестовой роли мог остаться
    users = client.get("/api/v1/users", headers=admin_headers).json()
    for u in users:
        if u["email"] == "storekeeper.roles-test@erp.local" and u["role"] != "readonly":
            client.patch(f"/api/v1/users/{u['id']}", json={"role": "readonly"},
                         headers=admin_headers)
    roles_before = {r["key"] for r in client.get("/api/v1/roles", headers=admin_headers).json()}
    if "kladovshchik-sklada" in roles_before:
        client.delete("/api/v1/roles/kladovshchik-sklada", headers=admin_headers)

    # создание: slug из названия
    created = client.post("/api/v1/roles", json={
        "name": "Кладовщик склада",
        "description": "Только склад",
        "color": "zinc",
    }, headers=admin_headers)
    assert created.status_code == 201
    role = created.json()
    assert role["key"] == "kladovshchik-sklada"
    assert role["is_builtin"] is False
    assert role["permissions"] == dict.fromkeys(MODULES + TOOL_MODULES, "none")

    # пользователь новой роли: доступ только что открытым модулям
    client.post("/api/v1/users", json={
        "email": "storekeeper.roles-test@erp.local",
        "password": "storekeeper1pass", "role": role["key"],
    }, headers=admin_headers)  # 409 на дубль между прогонами — не важно
    users = client.get("/api/v1/users", headers=admin_headers).json()
    sk = next(u for u in users if u["email"] == "storekeeper.roles-test@erp.local")
    client.patch(f"/api/v1/users/{sk['id']}", json={"role": role["key"]},
                 headers=admin_headers)  # повторный прогон: вернули на тестируемую роль
    storekeeper = _login(client, "storekeeper.roles-test@erp.local", "storekeeper1pass")

    assert client.get("/api/v1/integrations/connections",
                      headers=storekeeper).status_code == 403  # none
    client.put(f"/api/v1/roles/{role['key']}/permissions",
               json={"permissions": dict.fromkeys(MODULES + TOOL_MODULES, "none") | {"integrations": "ro"}},
               headers=admin_headers)
    assert client.get("/api/v1/integrations/connections",
                      headers=storekeeper).status_code == 200  # применено без перелогина
    assert client.post("/api/v1/integrations/webhooks", json={"name": "x"},
                       headers=storekeeper).status_code == 403  # ro ≠ rw

    # удаление роли с пользователем запрещено
    busy = client.delete(f"/api/v1/roles/{role['key']}", headers=admin_headers)
    assert busy.status_code == 409

    # перевод пользователя на другую роль — и роль можно удалить
    users = client.get("/api/v1/users", headers=admin_headers).json()
    sk = next(u for u in users if u["email"] == "storekeeper.roles-test@erp.local")
    patched = client.patch(f"/api/v1/users/{sk['id']}", json={"role": "readonly"},
                           headers=admin_headers)
    assert patched.status_code == 200
    deleted = client.delete(f"/api/v1/roles/{role['key']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.get("/api/v1/roles", headers=admin_headers).json() is not None

    # builtin удалить нельзя
    assert client.delete("/api/v1/roles/admin", headers=admin_headers).status_code == 400
    assert client.delete("/api/v1/roles/readonly", headers=admin_headers).status_code == 400


def test_role_duplicate_slug_conflict(client, admin_headers):
    first = client.post("/api/v1/roles", json={"name": "Тест-дубль"}, headers=admin_headers)
    assert first.status_code == 201
    second = client.post("/api/v1/roles", json={"name": "Тест-дубль"}, headers=admin_headers)
    assert second.status_code == 409
    assert client.delete(f"/api/v1/roles/{first.json()['key']}", headers=admin_headers).status_code == 200
