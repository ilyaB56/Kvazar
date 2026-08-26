"""Коннектор ЦБ РФ: ежедневные курсы (showcase-chain, этап C).

Источник: GET {base_url}/scripts/XML_daily.asp?date_req=DD/MM/YYYY
(базовый URL в config, дефолт https://www.cbr.ru). Парсинг XML:
Valute → CharCode, Nominal, Value (запятая — десятичный разделитель).

ВАЖНО: курс = Value / Nominal — деление обязательно: ₸, ₩ и др.
котируются за 10/100/1000 единиц. Результат: {date: ISO, rates:
[{currency, rate}]}, все числа строками (ADR-003).
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP

import httpx

from src.modules.integrations.sdk import BaseConnector, Capabilities, ConnectorResult, registry


def parse_cbr_xml(xml_text: str) -> dict:
    """Парсинг ответа XML_daily.asp. Чистая функция — тестируется на fixture."""
    root = ElementTree.fromstring(xml_text)
    day = datetime.strptime(root.get("Date", ""), "%d.%m.%Y").date()
    rates = []
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        nominal = Decimal((valute.findtext("Nominal") or "1").strip().replace(",", "."))
        value = Decimal((valute.findtext("Value") or "0").strip().replace(",", "."))
        if not code or nominal <= 0:
            continue
        rate = (value / nominal).quantize(Decimal("1e-8"), rounding=ROUND_HALF_UP)
        rates.append({"currency": code, "rate": format(rate.normalize(), "f")})
    return {"date": day.isoformat(), "rates": rates}


class CbrConnector(BaseConnector):
    code = "cbr"
    display_name = "ЦБ РФ (курсы валют)"
    capabilities = Capabilities(fetch=True)
    config_schema = {
        "base_url": {"type": "string", "default": "https://www.cbr.ru"},
        "timeout_seconds": {"type": "int", "default": 30},
    }

    def test_connection(self) -> ConnectorResult:
        result = self.fetch()
        return ConnectorResult(ok=result.ok, error=result.error)

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        # дата запроса: params['date'] (ISO) | endpoint (ISO или 'today') | сегодня
        raw = (params or {}).get("date") or (endpoint if endpoint and endpoint != "today" else "")
        try:
            day = date.fromisoformat(str(raw)) if raw else datetime.now(UTC).date()
        except ValueError:
            return ConnectorResult(ok=False, error=f"invalid date: {raw!r}")
        url = (self.config.get("base_url", "https://www.cbr.ru").rstrip("/") +
               "/scripts/XML_daily.asp")
        try:
            response = httpx.get(
                url, params={"date_req": day.strftime("%d/%m/%Y")},
                timeout=self.config.get("timeout_seconds", 30),
            )
            response.raise_for_status()
            return ConnectorResult(ok=True, data=parse_cbr_xml(response.text))
        except httpx.HTTPError as exc:
            return ConnectorResult(ok=False, error=str(exc))
        except ElementTree.ParseError as exc:
            return ConnectorResult(ok=False, error=f"CBR XML parse error: {exc}")


registry.register(CbrConnector)
