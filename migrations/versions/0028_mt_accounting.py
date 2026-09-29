"""Мультитенантность B1: company_id по mgmt_accounting (multitenancy-spec §5.3)

Revision ID: 0028
Revises: 0027

- company_id NOT NULL FK erp_core.companies (+ backfill «Основная»):
  accounts (колонка была nullable), categories, counterparties,
  transactions, periods, doc_sequences, items, locations, stock_moves,
  purchase_orders, sales_orders, receipts, shipments, production_orders,
  tech_cards
- составные UNIQUE вместо глобальных (Р2 — приватность нумерации):
  counterparties (company_id, internal_code); items (company_id, sku);
  locations (company_id, name); periods (company_id, year, month);
  doc_sequences (company_id, doc_type_code, year);
  transactions (company_id, doc_number); number-у документов:
  purchase_orders / sales_orders / receipts / shipments /
  production_orders → (company_id, number)
- ГЛОБАЛЬНЫЕ (без company_id): rates, doc_types, units;
  item_serials — код-актив, code_hash глобально уникален (фильтрация
  через item); *_lines — через родителя
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"
CORE = "erp_core"

# (таблица, колонка существующего UNIQUE или None)
TABLES = [
    "categories", "counterparties", "transactions", "periods",
    "doc_sequences", "items", "locations", "stock_moves",
    "purchase_orders", "sales_orders", "receipts", "shipments",
    "production_orders", "tech_cards",
]

# глобальный UNIQUE → составной: (таблица, имя старого, новый составной)
UNIQUES = [
    ("counterparties", "counterparties_internal_code_key",
     ["company_id", "internal_code"]),
    ("items", "items_sku_key", ["company_id", "sku"]),
    ("locations", "locations_name_key", ["company_id", "name"]),
    ("periods", "periods_year_month_key", ["company_id", "year", "month"]),
    ("doc_sequences", "doc_sequences_doc_type_code_year_key",
     ["company_id", "doc_type_code", "year"]),
    ("transactions", "transactions_doc_number_key",
     ["company_id", "doc_number"]),
    ("purchase_orders", "purchase_orders_number_key", ["company_id", "number"]),
    ("sales_orders", "sales_orders_number_key", ["company_id", "number"]),
    ("receipts", "receipts_number_key", ["company_id", "number"]),
    ("shipments", "shipments_number_key", ["company_id", "number"]),
    ("production_orders", "production_orders_number_key",
     ["company_id", "number"]),
]

MAIN = f"(SELECT id FROM {CORE}.companies WHERE name = 'Основная')"


def upgrade() -> None:
    # 1) колонки (nullable) + backfill. ВАЖНО: accounts.company_id уже
    # существует с 0003 (nullable, «v1 — одноконтурная система») — для
    # accounts НЕ add_column (на чистой базе падало DuplicateColumn,
    # инцидент CI exit 124: коробка не ставилась на чистую БД), а сразу
    # backfill + FK + NOT NULL. Симметрично downgrade: колонку не дропаем.
    for table in TABLES:
        op.add_column(table, sa.Column("company_id", UUID(as_uuid=True),
                                       nullable=True), schema=S)
    for table in TABLES + ["accounts"]:
        op.execute(f"UPDATE {S}.{table} SET company_id = {MAIN}"
                   f" WHERE company_id IS NULL")

    # 2) FK + NOT NULL
    for table in TABLES + ["accounts"]:
        op.create_foreign_key(
            f"fk_{table}_company", table, "companies",
            ["company_id"], ["id"], source_schema=S, referent_schema=CORE)
        op.alter_column(table, "company_id", nullable=False, schema=S)

    # api_tokens: контекст организации токена (наследуется от владельца;
    # платформенный админ в контексте org создаёт токен этой организации —
    # sales_flow и другие машинные интеграции пишут в данные организации)
    op.add_column("api_tokens",
                  sa.Column("company_id", UUID(as_uuid=True), nullable=True),
                  schema=CORE)
    op.create_foreign_key(
        "fk_api_tokens_company", "api_tokens", "companies",
        ["company_id"], ["id"], source_schema=CORE, referent_schema=CORE)

    # 3) глобальные UNIQUE → составные
    for table, old_name, cols in UNIQUES:
        op.drop_constraint(old_name, table, schema=S, type_="unique")
        op.create_unique_constraint(
            f"uq_{table}_company_" + "_".join(
                c for c in cols if c != "company_id"),
            table, cols, schema=S)


def downgrade() -> None:
    # составные → обратно глобальные (если организации размножили данные,
    # понизить нельзя из-за конфликтов — сначала свернуть до одной)
    for table, old_name, cols in UNIQUES:
        op.drop_constraint(
            f"uq_{table}_company_" + "_".join(
                c for c in cols if c != "company_id"),
            table, schema=S, type_="unique")
        original = cols[1:] if cols[0] == "company_id" else cols
        op.create_unique_constraint(old_name, table, original, schema=S)
    for table in TABLES:
        op.drop_constraint(f"fk_{table}_company", table, schema=S)
        op.drop_column(table, "company_id", schema=S)
    # accounts: колонка существовала до 0028 (nullable) — возвращаем
    # nullable и снимаем наш FK, саму колонку не дропаем
    op.drop_constraint("fk_accounts_company", "accounts", schema=S)
    op.alter_column("accounts", "company_id", nullable=True, schema=S)
    op.drop_constraint("fk_api_tokens_company", "api_tokens", schema=CORE)
    op.drop_column("api_tokens", "company_id", schema=CORE)
