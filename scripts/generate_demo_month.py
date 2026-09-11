"""Генератор демо-данных «месяц жизни компании» (мини-заезд, этап 2).

Черновик от erp-qa 2026-09-10 (статически выверен по API, НЕ прогнан —
первый прогон и доводка на дев-стенде за разработчиком; см.
docs/design/pilots-readiness.md этап 2 и чеклист заезда).

Только stdlib и только публичный API (никаких прямых записей в БД).
Стиль — tests/smoke.py: секции, call(), get_or_create(), итоговые сверки.

Env:
  DEMO_API_URL          (default http://localhost:8000)
  DEMO_ADMIN_EMAIL      (default admin@example.com)
  DEMO_ADMIN_PASSWORD   (default admin12345)
Флаги: --days N (default 30), --seed N (default 42), --fresh (печать плана чистки, выход).

Идемпотентность: префикс demo-<ГГГГММДД> — НСИ (счета/контрагенты/товары)
переиспользуется по имени при повторе того же дня; документы всегда создаются
заново (повторный запуск того же дня удвоит цепочки — см. --fresh).

Известное ограничение: заказы и сделки CRM создаются с серверным created_at
(«сегодня»); бэктейтинг поддерживают приёмки/отгрузки (moved_at), оплаты
(operated_at), курсы, коммуникации, задачи. Дашборды по operated_at/moved_at
показывают месяц корректно.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

p = argparse.ArgumentParser()
p.add_argument("--days", type=int, default=30)
p.add_argument("--seed", type=int, default=42)
p.add_argument("--fresh", action="store_true",
               help="напечатать план чистки стенда и выйти")
args = p.parse_args()

BASE = os.environ.get("DEMO_API_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("DEMO_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("DEMO_ADMIN_PASSWORD", "admin12345")

RUN_ID = "demo-" + date.today().strftime("%Y%m%d")
ACC = "/api/v1/accounting"
CRM = "/api/v1/crm"
rng = random.Random(args.seed)
FAILED = []
T0 = time.time()

if args.fresh:
    print("""ПЛАН ЧИСТИКИ СТЕНДА (--fresh, НЕ исполняется скриптом).
Рекомендуемый путь — пересоздать стенд целиком (отдельный compose-project):
  docker compose -p demo down -v && docker compose -p demo up -d --build
SQL-порядок по FK (референс, выполнять только вручную в psql):
  1) integrations: flow_runs, webhook_events, online_payments, item_mappings,
     sync_runs, notifications, recipes, sync_jobs, connections, webhook endpoints
  2) core: outbox(события), events_log/аудит, record_versions
  3) mini_crm: communications, activities, deals (stages — seed, не трогать)
  4) mgmt_accounting: item_serials, stock_moves, receipt_lines, receipts,
     shipment_lines, shipments, purchase_order_lines, purchase_orders,
     sales_order_lines, sales_orders, production_orders, tech_cards,
     transactions, accounts, categories, counterparties, contacts, items,
     locations (только созданные, не seed), doc_sequences
  5) core: api_tokens; users — всё кроме admin
Единственный безопасный полный сброс нумерации — down -v (doc_sequences в БД).""")
    sys.exit(0)


def call(method, path, body=None, token=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


TOKEN = ""


def login():
    """Логин 1 раз; повтор только по 401. Не брутфорсим:
    rate limit = 5 неудач/мин -> 429 на 60 с для всего IP."""
    global TOKEN
    st, data = call("POST", "/api/v1/auth/login",
                    {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if st != 200 or "access_token" not in data:
        sys.exit(f"FATAL: логин {st}: {str(data)[:200]}")
    TOKEN = data["access_token"]


def must(method, path, body=None, ok=(200, 201)):
    global TOKEN
    st, data = call(method, path, body, token=TOKEN)
    if st == 401:
        login()
        st, data = call(method, path, body, token=TOKEN)
    if st not in ok:
        raise RuntimeError(f"{method} {path} -> {st}: {str(data)[:300]}")
    return data


def get_or_create(path, match, body):
    items = must("GET", path)
    for it in items:
        if match(it):
            return it, False
    return must("POST", path, body, ok=(201,)), True


def money(dec):  # все суммы — Decimal-строки (ADR-003)
    return f"{Decimal(dec).quantize(Decimal('0.01'))}"


# ---------- 1. health, периоды ----------

st, data = call("GET", "/health")
if st != 200 or data.get("status") != "ok":
    sys.exit(f"FATAL: /health {st}: {str(data)[:200]}")
login()

DAYS = args.days
TODAY = date.today()
CAL = [TODAY - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]  # старые -> новые
months = sorted({(d.year, d.month) for d in CAL})
periods = must("GET", f"{ACC}/periods")
closed = [pr for pr in periods if pr.get("status") == "closed"
          and (pr["year"], pr["month"]) in months]
if closed:
    sys.exit("FATAL: в диапазоне есть закрытые периоды: "
             + ", ".join(f"{pr['year']}-{pr['month']:02d}" for pr in closed)
             + ". Переоткрытие — административная операция, решает оператор стенда.")

print(f"== Генерация {RUN_ID}: {DAYS} дней ({CAL[0]} .. {CAL[-1]}), seed={args.seed} ==")

# ---------- 2. НСИ (переиспользуется по имени при повторе того же дня) ----------

locations = {l["name"]: l for l in must("GET", f"{ACC}/locations")}
MAIN, DIG = locations["Основной склад"], locations["Цифровой склад"]

bank = get_or_create(f"{ACC}/accounts", lambda a: a["name"] == f"{RUN_ID}-Р/с",
                     {"name": f"{RUN_ID}-Р/с", "currency": "RUB",
                      "account_number": "40702810900000005555"})[0]
cash = get_or_create(f"{ACC}/accounts", lambda a: a["name"] == f"{RUN_ID}-Касса",
                     {"name": f"{RUN_ID}-Касса", "currency": "RUB"})[0]
PAY_ACC = [bank, cash]

SUPPLIERS = []
for name, inn in (("ООО ТехноПром", "7701111222"), ("ООО Логистик+", "7812333445"),
                  ("ИП Смирнов А.В.", "7801555666")):
    SUPPLIERS.append(get_or_create(
        f"{ACC}/counterparties", lambda c, n=name: c["name"] == f"{RUN_ID}-{n.strip()}",
        {"name": f"{RUN_ID}-{name.strip()}", "inn": inn})[0])

CLIENTS = []
for name in ("ООО Ромашка", "ООО Весна", "ИП Кузнецов",
             "ООО Северяночка", "ООО Дельта-Трейд", "ИП Ахметов",
             "ООО Строймир", "ООО БытТех", "ИФ Лидия"):
    CLIENTS.append(get_or_create(
        f"{ACC}/counterparties", lambda c, n=name: c["name"] == f"{RUN_ID}-{n}",
        {"name": f"{RUN_ID}-{name}"})[0])

# номенклатура: 8 физических + 2 материала + 1 изделие + 3 цифровых + 1 услуга
PHYS = []   # (item, base_cost, base_price)
for i, (name, cost, price) in enumerate((
        ("Виджет W-100", 1250, 2400), ("Виджет W-200", 2400, 4700),
        ("Кабель USB-C 1м", 180, 490), ("Кабель HDMI 2м", 320, 850),
        ("Мышь офисная", 550, 1290), ("Клавиатура мембранная", 900, 1990),
        ("Монитор 24\"", 9500, 15900), ("Док-станция USB", 4200, 7900)), 1):
    PHYS.append((get_or_create(
        f"{ACC}/items", lambda x, n=name: x["sku"] == f"{RUN_ID}-SKU-{i:02d}",
        {"sku": f"{RUN_ID}-SKU-{i:02d}", "name": name, "kind": "physical",
         "unit_code": "шт", "low_stock_threshold": "5"})[0],
        Decimal(cost), Decimal(price)))
M1 = get_or_create(f"{ACC}/items", lambda x: x["sku"] == f"{RUN_ID}-MAT-1",
                   {"sku": f"{RUN_ID}-MAT-1", "name": "Платформа базовая",
                    "kind": "physical", "unit_code": "шт"})[0]
M2 = get_or_create(f"{ACC}/items", lambda x: x["sku"] == f"{RUN_ID}-MAT-2",
                   {"sku": f"{RUN_ID}-MAT-2", "name": "Корпус компакт",
                    "kind": "physical", "unit_code": "шт"})[0]
PROD = get_or_create(f"{ACC}/items", lambda x: x["sku"] == f"{RUN_ID}-PRD-1",
                     {"sku": f"{RUN_ID}-PRD-1", "name": "Стенд Квазар-М",
                      "kind": "physical", "unit_code": "шт"})[0]
DIGS = []  # (item, cost, price)
for i, (name, cost, price) in enumerate((
        ("Код пополнения 500", 400, 550), ("Код пополнения 1000", 800, 990),
        ("Лицензия PRO год", 2500, 3900)), 1):
    DIGS.append((get_or_create(
        f"{ACC}/items", lambda x, n=name: x["sku"] == f"{RUN_ID}-DIG-{i}",
        {"sku": f"{RUN_ID}-DIG-{i}", "name": name, "kind": "digital",
         "unit_code": "лицензия"})[0], Decimal(cost), Decimal(price)))
SRV = get_or_create(f"{ACC}/items", lambda x: x["sku"] == f"{RUN_ID}-SRV-1",
                    {"sku": f"{RUN_ID}-SRV-1", "name": "Установка и настройка",
                     "kind": "service", "unit_code": "час"})[0]
STORNO_ITEM = get_or_create(f"{ACC}/items", lambda x: x["sku"] == f"{RUN_ID}-STO-1",
                            {"sku": f"{RUN_ID}-STO-1", "name": "Товар для сторно",
                             "kind": "physical", "unit_code": "шт"})[0]

card = get_or_create(f"{ACC}/tech-cards", lambda c: c["name"] == f"{RUN_ID}-сборка",
                     {"name": f"{RUN_ID}-сборка", "product_item_id": PROD["id"],
                      "qty_out": "1",
                      "components": [{"item_id": M1["id"], "qty": "2"},
                                     {"item_id": M2["id"], "qty": "4"}]})[0]

# локальная модель остатков (чтобы не ловить 422 insufficient_stock)
onhand, dig_cnt = {}, {}
for it, _, _ in PHYS:
    onhand[it["id"]] = Decimal(0)
onhand.update({M1["id"]: Decimal(0), M2["id"]: Decimal(0), PROD["id"]: Decimal(0),
               STORNO_ITEM["id"]: Decimal(0)})
counts = {"ЗП": 0, "ПМ": 0, "СК": 0, "ЗК": 0, "ОТ": 0, "ПК": 0, "СБ": 0, "СТ": 0,
          "adjust": 0, "crm_deal": 0, "crm_task": 0, "crm_comm": 0}


def day_purchase(d, sup, lines, currency="RUB", part_first="0.6"):
    """Заказ (создаётся 'сегодня', курс замораживается) + частичная приёмка в дату d."""
    po = must("POST", f"{ACC}/purchase-orders",
              {"counterparty_id": sup["id"], "currency": currency,
               "note": f"{RUN_ID}", "lines": lines})
    must("POST", f"{ACC}/purchase-orders/{po['id']}/confirm")
    counts["ЗП"] += 1
    first = [{"item_id": l["item_id"],
              "qty": str((Decimal(l["qty"]) * Decimal(part_first)).to_integral_value())}
             for l in lines]
    rc = must("POST", f"{ACC}/receipts",
              {"purchase_order_id": po["id"], "moved_at": d.isoformat(),
               "counterparty_doc": f"НАК-{d.strftime('%d%m')}-{rng.randint(100, 999)}",
               "lines": first})
    must("POST", f"{ACC}/receipts/{rc['id']}/post")
    counts["ПМ"] += 1
    for l in first:
        onhand[l["item_id"]] += Decimal(l["qty"])
    return po, rc


def day_receipt_rest(d, po, lines):
    rc = must("POST", f"{ACC}/receipts",
              {"purchase_order_id": po["id"], "moved_at": d.isoformat(), "lines": lines})
    must("POST", f"{ACC}/receipts/{rc['id']}/post")
    counts["ПМ"] += 1
    for l in lines:
        onhand[l["item_id"]] += Decimal(l["qty"])
    return rc


def day_sale(d, client, lines, crm_deal_id=None, ship_ratio="1", pay_ratio="1",
             account=None):
    """Заказ клиента (опц. из сделки) + отгрузка (частичная) + оплата (частичная/нет)."""
    body = {"counterparty_id": client["id"], "currency": "RUB",
            "note": f"{RUN_ID}", "lines": lines}
    if crm_deal_id:
        body["crm_deal_id"] = crm_deal_id
    so = must("POST", f"{ACC}/sales-orders", body)
    must("POST", f"{ACC}/sales-orders/{so['id']}/confirm")
    counts["ЗК"] += 1
    shippable = [l for l in lines if l["item_id"] != SRV["id"]]
    if shippable:
        shl = [{"item_id": l["item_id"],
                "qty": str((Decimal(l["qty"]) * Decimal(ship_ratio)).to_integral_value())}
               for l in shippable]
        shl = [l for l in shl if Decimal(l["qty"]) > 0]  # qty 1 × 0.5 → 0 (half-even)
        if shl:
            shp = must("POST", f"{ACC}/shipments",
                       {"sales_order_id": so["id"], "moved_at": d.isoformat(),
                        "lines": shl})
            must("POST", f"{ACC}/shipments/{shp['id']}/post")
            counts["ОТ"] += 1
            for l in shl:
                if l["item_id"] in onhand:
                    onhand[l["item_id"]] -= Decimal(l["qty"])
                else:
                    dig_cnt[l["item_id"]] -= int(Decimal(l["qty"]))
    if Decimal(pay_ratio) > 0:
        total = sum(Decimal(l["qty"]) * Decimal(l["unit_price"]) for l in lines)
        must("POST", f"{ACC}/sales-orders/{so['id']}/pay",
             {"account_id": (account or rng.choice(PAY_ACC))["id"],
              "amount": money(total * Decimal(pay_ratio)),
              "operated_at": d.isoformat()})
        counts["ПК"] += 1
    return so


# ---------- 3. Курсы за месяц (правдоподобное случайное блуждание) ----------

usd, eur = Decimal("89.4000"), Decimal("97.2500")
for d in CAL:
    usd += Decimal(rng.randint(-40, 45)) / 100
    eur += Decimal(rng.randint(-45, 50)) / 100
    must("POST", f"{ACC}/rates", {"date": d.isoformat(), "currency": "USD",
                                  "rate": f"{usd:.4f}"}, ok=(200,))
    must("POST", f"{ACC}/rates", {"date": d.isoformat(), "currency": "EUR",
                                  "rate": f"{eur:.4f}"}, ok=(200,))

# ---------- 4. Цикл по дням ----------

open_pos = {}        # po_id -> (дата, остаток строк для второй приёмки)
debtor_orders = []   # отгружено, но не оплачено (дебиторка)
sup_debt = []        # неоплаченные заказы поставщику (кредиторка)

for i, d in enumerate(CAL, 1):
    did = f"[{i:02d}/{DAYS} {d}]"

    # 4.1 стартовые закупки + еженедельные пополнения (физика)
    if i == 1:
        lines = [{"item_id": it["id"], "qty": str(rng.randint(24, 40)),
                  "unit_price": str(cost)} for it, cost, _ in PHYS]
        lines += [{"item_id": M1["id"], "qty": "40", "unit_price": "1500"},
                  {"item_id": M2["id"], "qty": "80", "unit_price": "350"}]
        po, _ = day_purchase(d, SUPPLIERS[0], lines, part_first="0.6")
        rest = [{"item_id": l["item_id"],
                 "qty": str(Decimal(l["qty"])
                            - (Decimal(l["qty"]) * Decimal("0.6")).to_integral_value())}
                for l in lines]
        open_pos[po["id"]] = (d + timedelta(days=2), rest, SUPPLIERS[0])
    if i in (8, 16, 24):
        need = [row for row in PHYS if onhand[row[0]["id"]] < 12]
        if need:
            day_purchase(d, rng.choice(SUPPLIERS[:2]),
                         [{"item_id": it["id"], "qty": str(rng.randint(15, 30)),
                           "unit_price": str(cost)} for it, cost, _ in need])
    # 4.2 вторые приёмки прошлых заказов
    for pid, (when, rest, sup) in list(open_pos.items()):
        if d >= when:
            day_receipt_rest(d, {"id": pid}, rest)
            sup_debt.append((pid, sup))
            del open_pos[pid]

    # 4.3 валютная закупка EUR (курс заморожен при создании заказа)
    if i == 5:
        po_eur, _ = day_purchase(
            d, SUPPLIERS[1],
            [{"item_id": PHYS[6][0]["id"], "qty": "8", "unit_price": "105.00"}],
            currency="EUR", part_first="0.75")
        sup_debt.append((po_eur["id"], SUPPLIERS[1]))

    # 4.4 цифровые коды: партии оприходованием с датой прихода
    if i in (1, 10, 20):
        for it, cost, _ in DIGS:
            batch = [f"{RUN_ID}-CODE-{it['sku'][-1]}-{d.strftime('%d%m')}-{n:03d}"
                     for n in range(1, rng.randint(12, 20))]
            try:
                must("POST", f"{ACC}/stock/adjustment",
                     {"location_id": DIG["id"], "moved_at": d.isoformat(),
                      "lines": [{"item_id": it["id"], "serial_codes": batch,
                                 "unit_cost": str(cost)}]})
            except RuntimeError as exc:  # партия уже оприходована прошлым прогоном
                if "no_change" not in str(exc):
                    raise
            counts["adjust"] += 1
            dig_cnt[it["id"]] = dig_cnt.get(it["id"], 0) + len(batch)

    # 4.5 продажи: 1–3 заказа в день, часть частичных отгрузок/оплат
    for _ in range(rng.randint(1, 3)):
        avail_phys = [(it, cost, price) for it, cost, price in PHYS
                      if onhand[it["id"]] >= 3]
        lines = []
        for it, cost, price in rng.sample(avail_phys,
                                          k=min(len(avail_phys), rng.randint(1, 3))):
            qty = rng.randint(1, min(4, int(onhand[it["id"]])))
            lines.append({"item_id": it["id"], "qty": str(qty),
                          "unit_price": str(price)})
        avail_dig = [(it, c, pr) for it, c, pr in DIGS
                     if dig_cnt.get(it["id"], 0) >= 2]
        if avail_dig and rng.random() < 0.4:
            it, c, pr = rng.choice(avail_dig)
            lines.append({"item_id": it["id"], "qty": str(rng.randint(1, 2)),
                          "unit_price": str(pr)})
        if rng.random() < 0.15:  # услуга — без движений, только деньги
            lines.append({"item_id": SRV["id"], "qty": "1", "unit_price": "4500"})
        if not lines:
            continue
        client = rng.choice(CLIENTS)
        r = rng.random()
        ship_ratio = "0.5" if r < 0.2 else "1"        # каждая пятая — частичная отгрузка
        pay_ratio = "0" if r >= 0.85 else ("0.4" if r < 0.35 else "1")  # ~15% не оплачено
        so = day_sale(d, client, lines, ship_ratio=ship_ratio, pay_ratio=pay_ratio)
        if pay_ratio == "0" and d <= CAL[-3]:
            debtor_orders.append((d, client, so["id"]))

    # 4.6 оплаты поставщикам (частично — остаётся кредиторка)
    if i in (7, 14, 21, 28) and sup_debt:
        for _ in range(rng.randint(1, 2)):
            pid, sup = sup_debt.pop(rng.randrange(len(sup_debt)))
            must("POST", f"{ACC}/purchase-orders/{pid}/pay",
                 {"account_id": bank["id"],
                  "amount": money(rng.randint(15000, 90000)),
                  "operated_at": d.isoformat()})
            counts["СК"] += 1

    # 4.7 сборки (2 за месяц) + продажа изделий в тот же день
    if i in (12, 24) and onhand[M1["id"]] >= 6 and onhand[M2["id"]] >= 12:
        q = "2"
        pro = must("POST", f"{ACC}/production-orders",
                   {"tech_card_id": card["id"], "qty_planned": q})
        must("POST", f"{ACC}/production-orders/{pro['id']}/post")
        counts["СБ"] += 1
        onhand[M1["id"]] -= Decimal(2) * Decimal(q)
        onhand[M2["id"]] -= Decimal(4) * Decimal(q)
        onhand[PROD["id"]] += Decimal(q)
        day_sale(d, rng.choice(CLIENTS[:4]),
                 [{"item_id": PROD["id"], "qty": "1", "unit_price": "12900"}])

    # 4.8 сторно-пары (2 за месяц, чистые: без последующих движений по товару)
    if i == 9:  # сторно приёмки: приняли-рассторнировали
        po = must("POST", f"{ACC}/purchase-orders",
                  {"counterparty_id": SUPPLIERS[2]["id"], "currency": "RUB",
                   "lines": [{"item_id": STORNO_ITEM["id"], "qty": "10",
                              "unit_price": "500"}]})
        must("POST", f"{ACC}/purchase-orders/{po['id']}/confirm")
        counts["ЗП"] += 1
        rc = must("POST", f"{ACC}/receipts",
                  {"purchase_order_id": po["id"], "moved_at": d.isoformat(),
                   "lines": [{"item_id": STORNO_ITEM["id"], "qty": "10"}]})
        must("POST", f"{ACC}/receipts/{rc['id']}/post")
        counts["ПМ"] += 1
        onhand[STORNO_ITEM["id"]] += 10
        must("POST", f"{ACC}/receipts/{rc['id']}/unpost",
             {"reason": f"{RUN_ID}: ошибка приёмки"})
        counts["СТ"] += 1
        onhand[STORNO_ITEM["id"]] -= 10
    if i == 18:  # сторно отгрузки: отгрузили-вернули тем же днём
        must("POST", f"{ACC}/stock/adjustment",
             {"location_id": MAIN["id"],
              "moved_at": (d - timedelta(days=1)).isoformat(),
              "lines": [{"item_id": STORNO_ITEM["id"], "qty_fact": "6",
                         "unit_cost": "500"}]})
        counts["adjust"] += 1
        onhand[STORNO_ITEM["id"]] += 6
        so = day_sale(d, rng.choice(CLIENTS),
                      [{"item_id": STORNO_ITEM["id"], "qty": "2",
                        "unit_price": "990"}], pay_ratio="1")
        shp = must("GET", f"{ACC}/shipments")
        mine = [s for s in shp if s.get("sales_order_id") == so["id"]][0]
        must("POST", f"{ACC}/shipments/{mine['id']}/unpost",
             {"reason": f"{RUN_ID}: возврат"})
        counts["СТ"] += 1
        onhand[STORNO_ITEM["id"]] += 2

    # 4.9 инвентаризация с недостачей (день 20)
    if i == 20:
        it = next(x for x, _, _ in PHYS if onhand[x["id"]] >= 10)
        fact = onhand[it["id"]] - 2
        must("POST", f"{ACC}/stock/adjustment",
             {"location_id": MAIN["id"], "moved_at": d.isoformat(),
              "lines": [{"item_id": it["id"], "qty_fact": str(fact)}]})
        counts["adjust"] += 1
        onhand[it["id"]] -= 2

    print(f"{did} ЗК={counts['ЗК']} ОТ={counts['ОТ']} ПМ={counts['ПМ']} "
          f"ПК={counts['ПК']} СК={counts['СК']} СБ={counts['СБ']}")

# ---------- 5. CRM: 6 сделок по стадиям + коммуникации + задачи ----------

stages = {s["name"]: s for s in must("GET", f"{CRM}/stages")}


def stage(nm):
    return stages.get(nm) or list(stages.values())[0]


deals_spec = [  # (title, amount, стадия_финал, клиент)
    ("Поставка виджетов ООО Ромашка", "180000", "Выиграна", CLIENTS[0]),
    ("Тендер Весна: парк мониторов", "450000", "Согласование", CLIENTS[1]),
    ("Кабельная партия Кузнецов", "60000", "Новая", CLIENTS[2]),
    ("Лицензии PRO: Северяночка", "120000", "Переговоры", CLIENTS[3]),
    ("Док-станции Дельта-Трейд", "95000", "Новая", CLIENTS[4]),
    ("Проект Строймир (дорого)", "800000", "Проиграна", CLIENTS[6]),
]
for k, (title, amount, fin, client) in enumerate(deals_spec):
    day = CAL[2 + k * 4] if 2 + k * 4 < DAYS else CAL[-1]
    deal = must("POST", f"{CRM}/deals",
                {"title": f"{RUN_ID}-{title}", "stage_id": stage("Новая")["id"],
                 "amount": amount, "currency": "RUB"})
    counts["crm_deal"] += 1
    for kind, text, dd in (("call", "Первичный звонок, обсудили бюджет", day),
                           ("email", "Отправил КП с ценами по прайсу",
                            day + timedelta(days=1)),
                           ("meeting", "Встреча в офисе, демо стенда Квазар",
                            day + timedelta(days=3))):
        must("POST", f"{CRM}/deals/{deal['id']}/communications",
             {"kind": kind, "content": text, "occurred_at": dd.isoformat()})
        counts["crm_comm"] += 1
    act = must("POST", f"{CRM}/deals/{deal['id']}/activities",
               {"title": "Подготовить КП v2", "due_at": day.isoformat()})
    counts["crm_task"] += 1
    if k in (1, 4):  # 1–2 просроченных: due в прошлом, не выполнено
        must("POST", f"{CRM}/deals/{deal['id']}/activities",
             {"title": "Уточнить условия доставки (просрочена)",
              "due_at": (TODAY - timedelta(days=2)).isoformat()})
        counts["crm_task"] += 1
    elif k == 0:
        must("PATCH", f"{CRM}/activities/{act['id']}", {"done": True})
    if fin != "Новая":
        must("POST", f"{CRM}/deals/{deal['id']}/move",
             {"stage_id": stage(fin)["id"]})

# выигранная сделка → заказ клиента с crm_deal_id (связь CRM-продажи)
# (кириллица в query — percent-encoding: py3.7 urllib не кодирует сам)
won = must("GET", f"{CRM}/deals?q="
           + urllib.parse.quote(f"{RUN_ID}-Поставка"))
won_deal = next((x for x in won if "Ромашка" in x["title"]), None)
if won_deal:
    day_sale(CAL[-2], CLIENTS[0],
             [{"item_id": PHYS[0][0]["id"], "qty": "3", "unit_price": "2300"}],
             crm_deal_id=won_deal["id"], pay_ratio="1")

print(f"Генерация завершена за {time.time() - T0:.0f} с; документов: "
      f"{sum(counts.values())}")

# ---------- 6. КОНТРОЛЬНЫЕ СВЕРКИ (оператор сверяет с дашбордом глазами) ----------

print("\n========== СВЕРКИ ==========")
ok_all = True


def verify(name, ok, detail=""):
    global ok_all
    print(f"{'OK ' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        ok_all = False
        FAILED.append(name)


F, T = CAL[0].isoformat(), CAL[-1].isoformat()
TOL = Decimal("0.05")  # копеечный допуск (после Д7/P10 у цифровых — per-code)

# 6.1 Σ остатков: по каждому товару Σqty×avg_cost = Σvalue
tot_qty, tot_val = Decimal(0), Decimal(0)
for it in [x[0] for x in PHYS] + [M1, M2, PROD] + [x[0] for x in DIGS]:
    item = must("GET", f"{ACC}/items/{it['id']}")
    bals = must("GET", f"{ACC}/stock/balances?item_id={it['id']}")
    q = sum(Decimal(b["qty"]) for b in bals)
    v = sum(Decimal(b["value"]) for b in bals)
    expected = (q * Decimal(item["avg_cost"] or 0)).quantize(Decimal("0.0001"))
    tot_qty += q
    tot_val += v
    verify(f"Σqty×avg = Σvalue: {item['sku']}", abs(v - expected) <= TOL,
           f"qty={q} value={v} avg={item['avg_cost']}")
print(f"ИТОГО остатки: qty={tot_qty}, value={tot_val:.2f} ₽")

# 6.2 касса (cashflow за месяц, по каждому счёту); тоталы ЗНАКОВЫЕ:
# income > 0, expense < 0 → closing − opening = Σincome + Σexpense
for acc in PAY_ACC:
    rep = must("GET",
               f"{ACC}/report/cashflow?date_from={F}&date_to={T}&account_id={acc['id']}")
    inc = sum(Decimal(t["total"]) for t in rep.get("totals", [])
              if t["kind"] == "income")
    exp = sum(Decimal(t["total"]) for t in rep.get("totals", [])
              if t["kind"] == "expense")
    delta = Decimal(rep["closing_balance"]) - Decimal(rep["opening_balance"])
    verify(f"касса {acc['name']}: closing−opening = Σдоходов+Σрасходов(зн.)",
           delta == (inc + exp).quantize(Decimal("0.01")),
           f"{rep['opening_balance']} → {rep['closing_balance']} "
           f"(income={inc}, expense={exp})")

# 6.3 продажи: выручка/себестоимость/маржа
srep = must("GET", f"{ACC}/reports/sales?date_from={F}&date_to={T}")
sh = srep.get("shipments", {})
rev, cogs = Decimal(sh.get("revenue_base", 0)), Decimal(sh.get("cogs_base", 0))
verify("маржа = выручка − себестоимость",
       Decimal(sh.get("margin_base", 0)) == (rev - cogs).quantize(Decimal("0.01")),
       f"revenue={rev} cogs={cogs} margin={sh.get('margin_base')}")

# 6.4 закупки по поставщикам (печать для сверки глазами)
prep = must("GET", f"{ACC}/reports/purchases?date_from={F}&date_to={T}")
print("Закупки по поставщикам:", json.dumps(
    [{k: r.get(k) for k in ("counterparty_id", "orders_amount_base",
                            "received_amount_base")}
     for r in prep.get("by_counterparty", [])], ensure_ascii=False))

# 6.5 сальдо пары контрагентов (кредиторка/дебиторка)
sup_sample = SUPPLIERS[0]
cb = must("GET",
          f"{ACC}/reports/counterparty-balance?counterparty_id={sup_sample['id']}"
          f"&on_date={T}")
print(f"Сальдо поставщика {sup_sample['name']}:", json.dumps(cb, ensure_ascii=False))
if debtor_orders:
    d0, c0, _ = debtor_orders[0]
    cb2 = must("GET",
               f"{ACC}/reports/counterparty-balance?counterparty_id={c0['id']}"
               f"&on_date={T}")
    print(f"Сальдо клиента-дебитора {c0['name']}:",
          json.dumps(cb2, ensure_ascii=False))
verify("дебиторка есть (не всё оплачено)", len(debtor_orders) >= 2,
       f"{len(debtor_orders)} заказов")

# 6.6 количество документов каждого типа (по префиксам номеров)
def count_docs(path, prefix):
    try:
        return len([x for x in must("GET", path)
                    if str(x.get("number") or x.get("doc_number")
                           or "").startswith(prefix)])
    except RuntimeError:
        return 0


doc_stat = {
    "Заказы поставщику (ЗП-)": count_docs(
        f"{ACC}/purchase-orders?date_from={F}&date_to={T}", "ЗП-"),
    "Приёмки (ПМ-)": count_docs(f"{ACC}/receipts?date_from={F}&date_to={T}", "ПМ-"),
    "Заказы клиентов (ЗК-)": count_docs(
        f"{ACC}/sales-orders?date_from={F}&date_to={T}", "ЗК-"),
    "Отгрузки (ОТ-)": count_docs(
        f"{ACC}/shipments?date_from={F}&date_to={T}", "ОТ-"),
    "Сборки (СБ-)": count_docs(f"{ACC}/production-orders", "СБ-"),
    "Оплаты вход. (ПК-)": count_docs(
        f"{ACC}/transactions?date_from={F}&date_to={T}", "ПК-"),
    "Оплаты исх. (СК-)": count_docs(
        f"{ACC}/transactions?date_from={F}&date_to={T}", "СК-"),
}
print("Документы за месяц:")
for k, v in doc_stat.items():
    print(f"  {k}: {v}")
total_docs = sum(doc_stat.values()) + counts["adjust"] + counts["crm_deal"]
verify("объём месяца 150–250 документов", 150 <= total_docs <= 250,
       f"всего {total_docs}")

# 6.7 CRM-воронка
pipe = must("GET", f"{CRM}/report/pipeline?date_from={F}&date_to={T}")
print("CRM pipeline:", json.dumps(pipe.get("totals", {}), ensure_ascii=False))
overdue = must("GET", f"{CRM}/activities?due_before={T}&status=open")
print(f"CRM: сделок={counts['crm_deal']}, задач={counts['crm_task']} "
      f"(открытых с дедлайном в прошлом: {len(overdue)})")

print("\nИТОГ:", "СВЕРКИ СХОДЯТСЯ" if ok_all else f"РАСХОЖДЕНИЯ: {FAILED}")
sys.exit(0 if ok_all else 1)
