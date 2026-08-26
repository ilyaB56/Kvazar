"""accounts.account_number для выгрузки клиент-банк 1С (showcase-chain, этап E)

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = "mgmt_accounting"


def upgrade() -> None:
    op.add_column("accounts",
                  sa.Column("account_number", sa.String(20)), schema=S)


def downgrade() -> None:
    op.drop_column("accounts", "account_number", schema=S)
