"""Справочники учёта: q-поиск + pg_trgm-индексы (гейт 1.1a)

Revision ID: 0021
Revises: 0020

ILIKE '%q%' по name счётов/категорий/контрагентов — GIN pg_trgm, как в CRM.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(f"CREATE INDEX ix_accounts_name_trgm ON {S}.accounts "
               "USING gin (name gin_trgm_ops)")
    op.execute(f"CREATE INDEX ix_categories_name_trgm ON {S}.categories "
               "USING gin (name gin_trgm_ops)")
    op.execute(f"CREATE INDEX ix_counterparties_name_trgm ON {S}.counterparties "
               "USING gin (name gin_trgm_ops)")
    # номенклатура: поиск по sku и имени — кладовщик ищет артикул
    op.execute(f"CREATE INDEX ix_items_sku_trgm ON {S}.items "
               "USING gin (sku gin_trgm_ops)")
    op.execute(f"CREATE INDEX ix_items_name_trgm ON {S}.items "
               "USING gin (name gin_trgm_ops)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_items_name_trgm")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_items_sku_trgm")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_counterparties_name_trgm")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_categories_name_trgm")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_accounts_name_trgm")
