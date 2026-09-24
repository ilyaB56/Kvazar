"""Оркестратор «платёж → документы» (sales-automation §3.2, этап B).

Шаги: counterparty → order → confirm → pay (этап C добавит ship →
deliver → notify). Шаговый flow_runs с продолжением: retry идёт с
последнего успешного шага, перечитывая context (шаги идемпотентны).
В учёт ходит через публичный API с X-API-Token (служебная учётка,
showcase-chain) — интеграции не трогают схемы модулей напрямую (ADR-001).

Ошибки §8: item_not_mapped / price_mismatch / period_closed → платёж в
manual, деньги учтены транзакцией без source-заказа (или по заказу —
см. таблицу §8); transaction_only для платежей без строк.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from src.core import events
from src.modules.integrations import models as m
from src.modules.integrations.connectors.egress import guarded_request

logger = logging.getLogger(__name__)

# фиксированные причины ошибок (§6 integration.payment.failed)
REASONS = ("item_not_mapped", "price_mismatch", "insufficient_stock",
           "period_closed", "mapping_error", "delivery_failed")

STEPS = ("counterparty", "order", "confirm", "pay", "ship", "deliver", "notify")


class FlowError(Exception):
    """Остановка флоу: reason из фиксированного набора, manual."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class AccountingApi:
    """Тонкий клиент публичного API учёта с X-API-Token (служебная учётка).

    Токен передаёт рецепт (connection http_rest с secret api_key) или
    конфиг sales_flow; v1 — тот же механизм, что и recipes_executor.
    """

    def __init__(self, base_url: str, api_token: str, timeout: int = 15):
        # нормализуем base: принимаем и "http://api:8000", и
        # "http://api:8000/api/v1" — пути методов относительные (без
        # /api/v1; раньше двойной префикс давал /api/v1/api/v1/… → 404)
        self._base = base_url.rstrip("/")
        if not self._base.endswith("/api/v1"):
            self._base += "/api/v1"
        self._headers = {"X-API-Token": api_token, "Content-Type": "application/json"}
        self._timeout = timeout

    def _call(self, method: str, path: str, json_body: dict | None = None) -> dict:
        # ADR-001: сеть только в connectors/ — ходим через guarded_request
        response = guarded_request(
            "sales_flow", method, f"{self._base}{path}", headers=self._headers,
            json_body=json_body, timeout=self._timeout,
        )
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("detail", body))[:200]
            except ValueError:
                detail = response.text[:200]
            raise FlowError(_map_http_error(response.status_code, detail),
                            f"{method} {path} → {response.status_code}: {detail}")
        return response.json() if response.content else {}

    def find_or_create_category(self, name: str) -> str:
        """Категория по имени (kind=expense), создаём при отсутствии —
        для статей Ozon (Комиссия/Логистика/Реклама)."""
        rows = self._call("GET", "/accounting/categories?limit=200")
        items = rows.get("items", rows) if isinstance(rows, dict) else rows
        for row in items or []:
            if row.get("name") == name and row.get("kind") == "expense":
                return row["id"]
        created = self._call("POST", "/accounting/categories",
                             {"name": name, "kind": "expense"})
        return created["id"]

    def create_expense(self, *, counterparty_id: str, category_id: str,
                       account_id: str, amount: str, operated_at: str,
                       description: str) -> dict:
        """Расход (kind=expense) с проведением; закрытый период → FlowError."""
        return self._call("POST", "/accounting/transactions", {
            "kind": "expense", "operated_at": operated_at or None,
            "amount": amount, "currency": "RUB",
            "account_id": account_id, "counterparty_id": counterparty_id,
            "category_id": category_id, "description": description,
            "post_immediately": True,
        })

    def default_account_id(self) -> str:
        """Первый RUB-счёт (для расходов Ozon без явного счёта)."""
        rows = self._call("GET", "/accounting/accounts?limit=50")
        items = rows.get("items", rows) if isinstance(rows, dict) else rows
        for row in items or []:
            if row.get("currency") == "RUB" and row.get("is_active", True):
                return row["id"]
        if items:
            return items[0]["id"]
        raise FlowError("no_account", "нет счетов для расходов Ozon")

    def find_or_create(self, buyer: dict, fallback_name: str) -> str:
        data = self._call("POST", "/accounting/counterparties/find-or-create", {
            "email": buyer.get("email", ""),
            "phone": buyer.get("phone", ""),
            "name": buyer.get("name", "") or fallback_name,
        })
        return data["counterparty_id"]

    def create_order(self, counterparty_id: str, lines: list[dict]) -> dict:
        return self._call("POST", "/accounting/sales-orders", {
            "counterparty_id": counterparty_id,
            "lines": lines,
        })

    def confirm_order(self, order_id: str) -> dict:
        return self._call("POST", f"/accounting/sales-orders/{order_id}/confirm", {})

    def pay_order(self, order_id: str, account_id: str, amount: str) -> dict:
        return self._call("POST", f"/accounting/sales-orders/{order_id}/pay", {
            "account_id": account_id, "amount": amount,
        })

    def create_shipment(self, order_id: str, lines: list[dict]) -> dict:
        return self._call("POST", "/accounting/shipments", {
            "sales_order_id": order_id, "lines": lines,
        })

    def post_shipment(self, shipment_id: str) -> dict:
        return self._call("POST", f"/accounting/shipments/{shipment_id}/post", {})

    def deliver_shipment(self, shipment_id: str, channel: str) -> dict:
        return self._call(
            "POST", f"/accounting/shipments/{shipment_id}/deliver",
            {"channel_note": channel})

    def shipment_codes(self, shipment_id: str) -> list[dict]:
        """Коды выданной отгрузки для шага notify (повторяемый): rw-токен
        видит расшифрованные serial_codes — расшифровка на лету по
        serial_ids (§12.5), открытые коды нигде не хранятся."""
        data = self._call("GET", f"/accounting/shipments/{shipment_id}")
        out = []
        for line in data.get("lines", []):
            if line.get("serial_codes"):
                out.append({"item_id": line["item_id"],
                            "codes": line["serial_codes"]})
        return out

    def create_transaction(self, counterparty_id: str, account_id: str,
                           amount: str, currency: str, description: str) -> dict:
        # транзакция без source-заказа (§12.3): деньги не ждут товара
        return self._call("POST", "/accounting/transactions", {
            "kind": "income", "operated_at": datetime.now(UTC).date().isoformat(),
            "amount": amount, "currency": currency, "account_id": account_id,
            "counterparty_id": counterparty_id, "description": description,
            "post_immediately": True,
        })


def _map_http_error(status: int, detail: str) -> str:
    """HTTP-ошибка учёта → фиксированная причина флоу (§8)."""
    if "insufficient_stock" in detail:
        return "insufficient_stock"
    if "период" in detail.lower() or "period" in detail.lower():
        return "period_closed"
    if "kind_mismatch" in detail or "not found" in detail.lower():
        return "mapping_error"
    return f"http_{status}"


def _resolve_lines(db, payment: m.OnlinePayment) -> list[dict]:
    """Строки платежа → строки заказа: маппинг external_item_id → sku →
    item_id; цена из платежа. Нет совпадения → FlowError(item_not_mapped)."""
    lines_out: list[dict] = []
    for line in payment.lines or []:
        external_id = str(line.get("external_id", "")).strip()
        sku = str(line.get("sku", "")).strip()
        mapping = None
        if external_id:
            mapping = db.scalar(select(m.ItemMapping).where(
                m.ItemMapping.connection_id == payment.connection_id,
                m.ItemMapping.external_item_id == external_id,
                m.ItemMapping.is_active.is_(True),
            ))
            if mapping is None:  # глобальный по sku
                mapping = db.scalar(select(m.ItemMapping).where(
                    m.ItemMapping.connection_id.is_(None),
                    m.ItemMapping.external_item_id == external_id,
                    m.ItemMapping.is_active.is_(True),
                ))
        if mapping is None and sku:
            mapping = db.scalar(select(m.ItemMapping).where(
                m.ItemMapping.connection_id == payment.connection_id,
                m.ItemMapping.sku == sku,
                m.ItemMapping.is_active.is_(True),
            ))
        if mapping is None:
            raise FlowError("item_not_mapped", f"external_id={external_id} sku={sku}")
        lines_out.append({
            "item_id": str(mapping.item_id),  # UUID → строка для JSON API
            "qty": str(line.get("qty", 1)),
            "unit_price": str(line.get("price", "0")),
            "_external": {"external_id": external_id, "sku": sku,
                          "price": str(line.get("price", "0")),
                          "name": line.get("name", "")},
        })
    return lines_out


def _check_prices(db, lines: list[dict], tolerance: Decimal) -> None:
    """price_mismatch (§8): цена платежа vs sale_price номенклатуры —
    эталон v1. sale_price не задан → сверять не с чем (пропускаем).
    Вызывается после pay: по §8 заказ подтверждается и деньги учтены,
    стоп — отгрузка/выдача (этап C не продолжит manual-платёж)."""
    from src.modules.mgmt_accounting.features.inventory import models as inv_m

    for line in lines:
        price = Decimal(str(line.get("unit_price", "0")))
        if price <= 0:
            raise FlowError("price_mismatch", f"non-positive price {price}")
        item = db.get(inv_m.Item, uuid.UUID(str(line["item_id"])))
        if item is None or item.sale_price is None:
            continue
        reference = Decimal(str(item.sale_price))
        if reference <= 0:
            continue
        diff = abs(reference - price)
        if diff > tolerance:
            raise FlowError(
                "price_mismatch",
                f"item {item.sku}: paid {price} vs sale_price {reference} "
                f"(tolerance {tolerance})")


def run_sales_flow(db, *, payment: m.OnlinePayment, recipe: m.Recipe | None,
                   api: AccountingApi) -> m.FlowRun:
    """Исполнить (или продолжить) флоу платежа. Все шаги идемпотентны:
    context хранит созданные id; retry пропускает готовое."""
    # сериализация конкурентных запусков: синхронный путь вебхука (в API)
    # и recipe_task (в воркере) могут прийти одновременно — без блокировки
    # строки два флоу гонятся по одним таблицам (deadlock/двойные документы)
    payment = db.execute(
        select(m.OnlinePayment).where(m.OnlinePayment.id == payment.id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if payment is None:
        raise FlowError("payment_not_found", "payment disappeared")
    # диагностика 403 из воркера: видим, какой токен реально уходит
    # (префикс + длина; сам секрет в логи не пишем)
    _token = getattr(api, "_headers", {}).get("X-API-Token", "")
    logger.info("sales_flow api token: %s… (len=%d)",
                _token[:10], len(_token))
    definition = (recipe.definition or {}) if recipe else {}
    action = definition.get("action", {})
    config = action.get("config", {}) if isinstance(action, dict) else {}
    account_id = str(config.get("account_id", ""))
    tolerance = Decimal(str(config.get("price_tolerance", "0")))
    on_no_items = config.get("on_no_items", "transaction_only")
    fallback_name = config.get("buyer_fallback_name", "Покупатель сайта")

    run = db.scalar(select(m.FlowRun).where(m.FlowRun.payment_id == payment.id))
    if run is None:
        run = m.FlowRun(payment_id=payment.id, recipe_id=recipe.id if recipe else None)
        db.add(run)
    elif run.status == "done" and payment.status == "processed":
        # флоу уже завершён (синхронный путь вебхука); recipe_task из
        # outbox-события должен молча завершиться — повторная запись
        # flow_runs гонится с живой транзакцией (deadlock)
        return run
    else:
        run.attempts += 1
        run.status = "running"
        run.error = ""
    ctx = dict(run.context or {})
    db.flush()

    def fail(reason: str, detail: str):
        run.status = "manual"
        run.error = f"{reason}: {detail}"[:500]
        run.context = ctx  # retry продолжит с достигнутого шага
        # §8: при delivery_failed учёт уже завершён — платёж processed
        # (manual только у флоу, retry повторит notify)
        payment.status = "processed" if reason == "delivery_failed" else "manual"
        payment.error_step = run.step
        payment.error_reason = reason
        events.publish(db, "integration.payment.failed", {
            "payment_id": str(payment.id),
            "provider_payment_id": payment.provider_payment_id,
            "step": run.step, "reason": reason,
        })

    try:
        # ---- counterparty ----
        if "counterparty" not in ctx:
            cp_id = str(api.find_or_create(payment.buyer or {}, fallback_name))
            ctx["counterparty_id"] = cp_id
            run.step = "counterparty"
            run.context = ctx
            db.flush()
        lines = _resolve_lines(db, payment)

        if not lines and on_no_items == "transaction_only":
            # §8: оплата без товарных строк — только транзакция + контрагент
            if "transaction_id" not in ctx:
                txn = api.create_transaction(
                    str(ctx["counterparty_id"]), str(account_id),
                    str(payment.amount), payment.currency,
                    f"Онлайн-оплата {payment.provider} {payment.provider_payment_id} (без строк)",
                )
                ctx["transaction_id"] = txn.get("id")
                payment.transaction_id = uuid.UUID(txn["id"]) if txn.get("id") else None
                run.context = ctx
            run.step = "pay"
            _finish(db, run, payment, ctx)
            return run

        # ---- order ----
        if "order_id" not in ctx:
            order = api.create_order(str(ctx["counterparty_id"]), [
                {"item_id": str(line["item_id"]), "qty": str(line["qty"]),
                 "unit_price": str(line["unit_price"])} for line in lines])
            ctx["order_id"] = order["id"]
            ctx["order_number"] = order.get("number")
            run.step = "order"
            run.context = ctx
            db.flush()
        payment.sales_order_id = uuid.UUID(ctx["order_id"])

        # ---- confirm ----
        if "confirmed" not in ctx:
            confirmed = api.confirm_order(ctx["order_id"])
            ctx["confirmed"] = True
            ctx["order_number"] = confirmed.get("number") or ctx.get("order_number")
            run.step = "confirm"
            run.context = ctx
            db.flush()

        # ---- pay ----
        if "transaction_id" not in ctx:
            txn = api.pay_order(str(ctx["order_id"]), str(account_id), str(payment.amount))
            ctx["transaction_id"] = txn.get("id")
            payment.transaction_id = uuid.UUID(txn["id"]) if txn.get("id") else None
            run.step = "pay"
            run.context = ctx
            db.flush()

        # price_mismatch ПОСЛЕ pay (§8): деньги учтены, стоп — отгрузка;
        # шаги уже созданы — manual помешает этапу C продолжить автоматически
        _check_prices(db, lines, tolerance)

        digital_only = all(
            _item_kind(db, line["item_id"]) == "digital" for line in lines
        ) if lines else False
        delivery_channel = config.get("delivery_channel", "none")

        # ---- ship (только товарные строки; §8 insufficient_stock → manual).
        # Создание и проведение — раздельные маркеры: падение на post
        # оставляет draft-отгрузку, retry доводит её, не создавая новую
        if lines and "shipment_id" not in ctx:
            shipment = api.create_shipment(str(ctx["order_id"]), [
                {"item_id": str(line["item_id"]), "qty": str(line["qty"])}
                for line in lines
            ])
            ctx["shipment_id"] = shipment["id"]
            run.context = ctx
            db.flush()
        if lines and "shipment_posted" not in ctx:
            posted = api.post_shipment(str(ctx["shipment_id"]))
            ctx["shipment_number"] = posted.get("number")
            ctx["shipment_posted"] = True
            run.step = "ship"
            run.context = ctx
            db.flush()
        payment.shipment_id = uuid.UUID(ctx["shipment_id"]) if ctx.get("shipment_id") else None

        # ---- deliver: разовая выдача кодов (аудит в учёте) ----
        if digital_only and delivery_channel != "none" and "delivered" not in ctx:
            api.deliver_shipment(str(ctx["shipment_id"]), delivery_channel)
            ctx["delivered"] = True
            run.step = "deliver"
            run.context = ctx
            db.flush()

        # ---- notify: повторяемый — коды читаются по serial_ids на лету ----
        if digital_only and delivery_channel != "none":
            run.step = "notify"  # наблюдаемость: шаг виден и при ожидании, и в fail()
            db.flush()
            _notify(db, api=api, payment=payment, ctx=ctx,
                    channel=delivery_channel, recipe_config=config)

        _finish(db, run, payment, ctx)
        return run
    except FlowError as exc:
        fail(exc.reason, exc.detail)
        db.commit()
        return run


def _finish(db, run: m.FlowRun, payment: m.OnlinePayment, ctx: dict):
    run.status = "done"
    run.finished_at = datetime.now(UTC)
    run.context = ctx
    payment.status = "processed"
    payment.updated_at = datetime.now(UTC)
    events.publish(db, "integration.payment.processed", {
        "payment_id": str(payment.id),
        "provider_payment_id": payment.provider_payment_id,
        "sales_order_id": ctx.get("order_id"),
        "transaction_id": ctx.get("transaction_id"),
        "shipment_id": None,
    })
    db.commit()
    events.dispatch_outbox(db)




def _item_kind(db, item_id) -> str | None:
    from src.modules.mgmt_accounting.features.inventory import models as inv_m

    item = db.get(inv_m.Item, uuid.UUID(str(item_id)))
    return item.kind if item else None


def _render_message(payment, codes: list[dict]) -> tuple[str, str]:
    """Письмо покупателю: тема + текст с кодами (коды покидают систему
    только этим письмом — §3.1.6)."""
    lines_text = "\n".join(
        f"• {block.get('sku', '')}: {', '.join(block['codes'])}"
        for block in codes
    ) or "(без товарных позиций)"
    subject = f"Ваш заказ {payment.provider}:{payment.provider_payment_id} — коды доступа"
    body = (
        "Здравствуйте!\n\n"
        "Спасибо за оплату. Ваши коды доступа:\n\n"
        f"{lines_text}\n\n"
        "Коды одноразовые — сохраните это письмо.\n"
        f"Заказ: {payment.provider} {payment.provider_payment_id}\n"
    )
    return subject, body


def _notify(db, *, api: AccountingApi, payment, ctx: dict,
            channel: str, recipe_config: dict) -> None:
    """Доставка кодов покупателю по каналу рецепта. Повторяемый шаг:
    коды перечитываются по serial_ids (расшифровка на лету rw-токеном).
    Ошибка доставки — delivery_failed (§8): учёт уже done, retry
    повторит только notify."""
    buyer = payment.buyer or {}
    email = str(buyer.get("email", "")).strip()
    codes = api.shipment_codes(str(ctx["shipment_id"])) if ctx.get("shipment_id") else []
    subject, body = _render_message(payment, codes)

    errors: list[str] = []
    if channel in ("email", "both") and email:
        smtp_cfg = recipe_config.get("smtp_connection_id")
        result = _send_email(db, smtp_cfg, to=email, subject=subject, text=body)
        if not result:
            errors.append("email_failed")
    if channel in ("telegram", "both"):
        # канал v1: chat_id из конфига (покупатель не даёт боту свой chat);
        # коды уходят сообщением бота
        tg_cfg_chat = str(recipe_config.get("telegram_chat_id", "")).strip()
        if tg_cfg_chat:
            sent = _send_telegram(db, chat_id=tg_cfg_chat,
                                  text=f"{subject}\n\n{body}")
            if not sent:
                errors.append("telegram_failed")
    if errors:
        raise FlowError("delivery_failed", "; ".join(errors))


def _send_email(db, smtp_connection_id, *, to: str, subject: str, text: str) -> bool:
    from .crypto import decrypt_dict

    conn = db.get(m.Connection, uuid.UUID(str(smtp_connection_id)))         if smtp_connection_id else db.scalar(select(m.Connection).where(
            m.Connection.connector_code == "smtp", m.Connection.is_active.is_(True)))
    if conn is None:
        logger.warning("notify: smtp connection not configured")
        return False
    from .connectors.builtin import registry as connector_registry
    connector = connector_registry.build(
        conn.connector_code, conn.config, decrypt_dict(conn.credentials_enc))
    result = connector.push(params={"to": to, "subject": subject, "text": text})
    if not result.ok:
        logger.warning("notify email failed: %s", result.error[:200])
    return result.ok


def _send_telegram(db, *, chat_id: str, text: str) -> bool:
    from .crypto import decrypt_dict

    conn = db.scalar(select(m.Connection).where(
        m.Connection.connector_code == "telegram", m.Connection.is_active.is_(True)))
    if conn is None:
        logger.warning("notify: telegram connection not configured")
        return False
    from .connectors.builtin import registry as connector_registry
    connector = connector_registry.build(
        conn.connector_code, conn.config, decrypt_dict(conn.credentials_enc))
    result = connector.push(endpoint="sendMessage", params={
        "chat_id": chat_id, "text": text[:4000],
    })
    if not result.ok:
        logger.warning("notify telegram failed: %s", result.error[:200])
    return result.ok


def normalize_payment(db, *, connection, connector, event: m.WebhookEvent) -> m.OnlinePayment | None:
    """Вебхук (verified) → online_payments + событие received; идемпотентно
    по UNIQUE(connection, provider_payment_id)."""
    payload = event.payload or {}
    normalized = connector.normalize(payload) if hasattr(connector, "normalize") else None
    if not normalized or not normalized.get("payment_id"):
        return None
    existing = db.scalar(select(m.OnlinePayment).where(
        m.OnlinePayment.connection_id == connection.id,
        m.OnlinePayment.provider_payment_id == normalized["payment_id"],
    ))
    if existing is not None:
        return existing
    payment = m.OnlinePayment(
        company_id=connection.company_id,
        connection_id=connection.id,
        provider=connection.connector_code,
        provider_payment_id=normalized["payment_id"],
        amount=Decimal(str(normalized.get("amount", "0"))),
        currency=normalized.get("currency", "RUB")[:3],
        buyer=normalized.get("buyer") or {},
        lines=normalized.get("lines") or [],
        metadata_json=normalized.get("metadata") or {},
        webhook_event_id=event.id,
    )
    db.add(payment)
    db.flush()
    events.publish(db, "integration.payment.received", {
        "payment_id": str(payment.id),
        "provider": payment.provider,
        "provider_payment_id": payment.provider_payment_id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "lines_count": len(payment.lines),
    })
    db.commit()
    events.dispatch_outbox(db)
    return payment
