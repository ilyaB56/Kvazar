"""inventory (resources-core, этап A): НСИ + склад-ядро

Таблицы units/items/locations/stock_moves/item_serials (схема mgmt_accounting,
ADR-007), seed единиц и локаций, представление остатков v_stock_balances.

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"

UNIT_SEED = [
    ("шт", "штука"),
    ("кг", "килограмм"),
    ("л", "литр"),
    ("м", "метр"),
    ("час", "час"),
    ("мес", "месяц"),
    ("лицензия", "лицензия/код цифрового товара"),
]

# Склады + системные транзиты (§3.2): транзиты не редактируются — API
# локаций не открывает их изменение; совместимость определяется по товару
LOCATION_SEED = [
    ("Основной склад", "physical", False),
    ("Цифровой склад", "digital", False),
    ("Поставщик", "physical", True),
    ("Клиент", "physical", True),
    ("Производство", "physical", True),
    ("Брак", "physical", True),
]


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.create_table(
        "items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sku", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),  # physical|digital|service
        sa.Column("unit_code", sa.String(20), sa.ForeignKey(f"{S}.units.code"), nullable=False),
        sa.Column("tracking", sa.String(10), nullable=False, server_default="qty"),
        sa.Column("barcode", sa.String(64)),
        sa.Column("sale_price", sa.Numeric(20, 4)),
        sa.Column("avg_cost", sa.Numeric(20, 4)),
        sa.Column("low_stock_threshold", sa.Numeric(20, 4)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('physical', 'digital', 'service')", name="ck_items_kind"
        ),
        sa.CheckConstraint("tracking IN ('qty', 'serial')", name="ck_items_tracking"),
        schema=S,
    )
    op.create_table(
        "locations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("is_transit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('physical', 'digital')", name="ck_locations_kind"),
        schema=S,
    )
    op.create_table(
        "stock_moves",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("qty", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 4)),
        sa.Column("from_location_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.locations.id"), nullable=False),
        sa.Column("to_location_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.locations.id"), nullable=False),
        sa.Column("counterparty_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.counterparties.id")),
        sa.Column("source_type", sa.String(30)),
        sa.Column("source_id", UUID(as_uuid=True)),
        sa.Column("moved_at", sa.Date(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("erp_core.users.id"),
                  nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0", name="ck_stock_moves_qty_positive"),
        sa.CheckConstraint("from_location_id <> to_location_id",
                           name="ck_stock_moves_not_same_location"),
        schema=S,
    )
    op.create_index("ix_stock_moves_item_from", "stock_moves",
                    ["item_id", "from_location_id"], schema=S)
    op.create_index("ix_stock_moves_item_to", "stock_moves",
                    ["item_id", "to_location_id"], schema=S)
    op.create_index("ix_stock_moves_moved_at", "stock_moves", ["moved_at"], schema=S)
    op.create_table(
        "item_serials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.items.id"),
                  nullable=False),
        sa.Column("code_enc", sa.Text(), nullable=False),  # Fernet
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),  # sha256
        sa.Column("status", sa.String(10), nullable=False, server_default="in_stock"),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.locations.id")),
        sa.Column("unit_cost", sa.Numeric(20, 4)),
        sa.Column("received_move_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{S}.stock_moves.id")),
        sa.Column("sold_move_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.stock_moves.id")),
        sa.Column("received_at", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_stock', 'reserved', 'sold', 'void')",
            name="ck_item_serials_status",
        ),
        schema=S,
    )
    op.create_index("ix_item_serials_item", "item_serials", ["item_id"], schema=S)

    # Остатки = Σ входов − Σ выходов, развёрнуто в потоки (двойная запись §2.2)
    op.execute(f"""
        CREATE VIEW {S}.v_stock_balances AS
        WITH flows AS (
            SELECT item_id, to_location_id AS location_id, qty
            FROM {S}.stock_moves
            UNION ALL
            SELECT item_id, from_location_id AS location_id, -qty
            FROM {S}.stock_moves
        )
        SELECT
            f.item_id,
            i.sku,
            f.location_id,
            l.name AS location_name,
            l.kind AS location_kind,
            SUM(f.qty) AS qty,
            i.avg_cost,
            SUM(f.qty) * i.avg_cost AS value
        FROM flows f
        JOIN {S}.items i ON i.id = f.item_id
        JOIN {S}.locations l ON l.id = f.location_id
        GROUP BY f.item_id, i.sku, f.location_id, l.name, l.kind, i.avg_cost
    """)

    for code, name in UNIT_SEED:
        op.execute(
            f"INSERT INTO {S}.units (code, name) VALUES ('{code}', '{name}')"
        )
    for name, kind, is_transit in LOCATION_SEED:
        op.execute(
            f"INSERT INTO {S}.locations (id, name, kind, is_transit) "
            f"VALUES (gen_random_uuid(), '{name}', '{kind}', {is_transit})"
        )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {S}.v_stock_balances")
    op.drop_table("item_serials", schema=S)
    op.drop_table("stock_moves", schema=S)
    op.drop_table("locations", schema=S)
    op.drop_table("items", schema=S)
    op.drop_table("units", schema=S)
