"""shipment_lines: serial_ids вместо открытых кодов (security, пост-гейт)

Revision ID: 0022
Revises: 0021

В строке отгрузки храним ссылки на item_serials (UUID), не расшифрованные
коды. Миграция: posted-строки → серийники по sold_move_id движения выдачи;
draft-строки → резолв открытых кодов по code_hash (in_stock). После
конвертации serial_codes очищается (колонка остаётся для отката).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column(
        "shipment_lines",
        sa.Column("serial_ids", sa.dialects.postgresql.JSONB(), nullable=True),
        schema=S,
    )

    # posted-строки: серийники выдачи привязаны к движению (sold_move_id)
    op.execute(f"""
        UPDATE {S}.shipment_lines sl
        SET serial_ids = sub.ids
        FROM (
            SELECT sl2.id AS line_id,
                   (json_agg(s.id)::jsonb) AS ids
            FROM {S}.shipment_lines sl2
            JOIN {S}.shipments sh ON sh.id = sl2.shipment_id
            JOIN {S}.stock_moves mv ON mv.source_type = 'shipment'
                 AND mv.source_id = sh.id AND mv.item_id = sl2.item_id
            JOIN {S}.item_serials s ON s.sold_move_id = mv.id
            WHERE sh.status = 'posted' AND sh.is_stornoed = false AND sl2.serial_ids IS NULL
            GROUP BY sl2.id
        ) AS sub
        WHERE sl.id = sub.line_id AND sl.serial_ids IS NULL
    """)

    # draft-строки: коды открытым текстом → in_stock серийники по отпечатку
    op.execute(f"""
        UPDATE {S}.shipment_lines sl
        SET serial_ids = sub.ids
        FROM (
            SELECT sl2.id AS line_id,
                   (json_agg(s.id)::jsonb) AS ids
            FROM {S}.shipment_lines sl2
            JOIN {S}.shipments sh ON sh.id = sl2.shipment_id
            CROSS JOIN LATERAL jsonb_array_elements_text(sl2.serial_codes) AS code(value)
            JOIN {S}.item_serials s ON s.code_hash = encode(digest(code.value, 'sha256'), 'hex')
            WHERE sh.status = 'draft' AND jsonb_typeof(sl2.serial_codes) = 'array'
            GROUP BY sl2.id
        ) AS sub
        WHERE sl.id = sub.line_id AND sl.serial_ids IS NULL
    """)


    # фаза 3: сторно-отгрузки — серийники вернулись в in_stock, привязки к
    # движению нет; резолв кодов по отпечатку независимо от статуса
    op.execute(f"""
        UPDATE {S}.shipment_lines sl
        SET serial_ids = sub.ids
        FROM (
            SELECT sl2.id AS line_id,
                   (json_agg(DISTINCT s.id)::jsonb) AS ids
            FROM {S}.shipment_lines sl2
            CROSS JOIN LATERAL jsonb_array_elements_text(sl2.serial_codes) AS code(value)
            JOIN {S}.item_serials s ON s.code_hash = encode(digest(code.value, 'sha256'), 'hex')
            WHERE jsonb_typeof(sl2.serial_codes) = 'array' AND sl2.serial_ids IS NULL
            GROUP BY sl2.id
        ) AS sub
        WHERE sl.id = sub.line_id AND sl.serial_ids IS NULL
    """)

    # открытые коды убираем из хранилища строк
    op.execute(f"UPDATE {S}.shipment_lines SET serial_codes = NULL WHERE serial_ids IS NOT NULL")


def downgrade() -> None:
    # Обратный переход требует расшифровки Fernet (code_enc → код) — в SQL
    # невозможен. posted-строки восстановимы сервисным слоем через
    # sold_move_id; коды draft-строк, увы, не восстанавливаются — потому
    # переход на serial_ids осознанно односторонний для draft-данных.
    op.execute(f"UPDATE {S}.shipment_lines SET serial_ids = NULL WHERE serial_ids IS NOT NULL")
    op.drop_column("shipment_lines", "serial_ids", schema=S)
