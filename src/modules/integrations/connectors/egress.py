"""Журнал исходящих обращений коннекторов (P1 security-plan п.7).

Каждый сетевой вызов коннектора пишется в erp_core.audit_events
(action='egress.request', payload={connector, host, status}) — без тела
и секретов. Домен сверяется с allowlist из настроек: строгий режим
запрещает обращение к домену вне списка (ConnectorError до запроса).

Использование из коннекторов: guarded_get()/guarded_post() — обёртки
httpx с проверкой и журналом. Существующие коннекторы переходят на них
поэтапно; ЮKassa-адаптер ходит только через них с этапа A.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from src.config import get_settings
from src.modules.integrations.sdk import ConnectorResult

logger = logging.getLogger(__name__)


class EgressBlocked(PermissionError):
    """Домен вне allowlist и включён строгий режим (P1 п.7)."""


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def _allowed(host: str, allowlist: list[str]) -> bool:
    if not host:
        return False
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in allowlist if domain
    )


def check_egress(url: str) -> None:
    """Разрешён ли домен: строгий режим запрещает всё вне списка."""
    settings = get_settings()
    allowlist = [d.strip().lower() for d in settings.connector_allowlist.split(",")]
    host = _host(url).lower()
    if not _allowed(host, allowlist) and settings.connector_allowlist_strict:
        raise EgressBlocked(f"host {host} is not in the connector allowlist")


def log_egress(*, connector: str, url: str, status: int | str, error: str = "") -> None:
    """Журнал исходящего обращения: домен/время/статус, без тела и секретов."""
    logger.info(
        "egress.request connector=%s host=%s status=%s error=%s",
        connector, _host(url), status, error[:120],
    )
    # постоянная запись — audit_events ядра; сессию создаём свою, чтобы не
    #мешать транзакции вызывающего кода
    try:
        from src.core.models import AuditEvent
        from src.db import SessionLocal

        db = SessionLocal()
        try:
            db.add(AuditEvent(
                action="egress.request",
                entity_type="connector",
                entity_id=connector,
                payload={
                    "host": _host(url),
                    "status": str(status),
                    "error": error[:200],
                    "at": datetime.now(UTC).isoformat(),
                },
            ))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — журнал не должен ломать вызов
        logger.warning("egress log write failed", exc_info=True)


def guarded_get(connector_code: str, url: str, *, headers: dict | None = None,
                params: dict | None = None, timeout: int = 15) -> httpx.Response:
    """GET с проверкой allowlist и журналом (для коннекторов)."""
    check_egress(url)
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=timeout)
        log_egress(connector=connector_code, url=url, status=response.status_code)
        return response
    except httpx.HTTPError as exc:
        log_egress(connector=connector_code, url=url, status="error", error=str(exc))
        raise


def guarded_post(connector_code: str, url: str, *, headers: dict | None = None,
                 json: dict | None = None, timeout: int = 15) -> httpx.Response:
    """POST с проверкой allowlist и журналом (для коннекторов)."""
    check_egress(url)
    try:
        response = httpx.post(url, headers=headers, json=json, timeout=timeout)
        log_egress(connector=connector_code, url=url, status=response.status_code)
        return response
    except httpx.HTTPError as exc:
        log_egress(connector=connector_code, url=url, status="error", error=str(exc))
        raise


def guarded_request(connector_code: str, method: str, url: str, *,
                    headers: dict | None = None, json_body: dict | None = None,
                    timeout: int = 15) -> httpx.Response:
    """Произвольный HTTP-метод с allowlist и журналом (для оркестраторов
    модуля integrations — самим оркестраторам импортировать httpx нельзя,
    ADR-001)."""
    check_egress(url)
    try:
        response = httpx.request(method, url, headers=headers, json=json_body,
                                 timeout=timeout)
        log_egress(connector=connector_code, url=url, status=response.status_code)
        return response
    except httpx.HTTPError as exc:
        log_egress(connector=connector_code, url=url, status="error", error=str(exc))
        raise


def guarded_result(connector_code: str, url: str, call) -> ConnectorResult:
    """Обёртка результата с журналом исключений сети."""
    try:
        return call()
    except EgressBlocked as exc:
        return ConnectorResult(ok=False, error=str(exc))
