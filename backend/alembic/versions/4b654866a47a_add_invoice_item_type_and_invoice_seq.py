"""add_invoice_item_type_and_invoice_seq

Revision ID: 4b654866a47a
Revises: ac7cbbeb9162
Create Date: 2026-11-10 21:51:40.007261

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4b654866a47a'
down_revision: Union[str, Sequence[str], None] = 'ac7cbbeb9162'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_invoice_item_type_enum = postgresql.ENUM(
    'base_subscription',
    'plan_upgrade',
    'proration_credit',
    'proration_debit',
    'usage',
    'tax',
    name='invoice_item_type_enum',
)


def upgrade() -> None:
    """Upgrade schema."""
    _invoice_item_type_enum.create(op.get_bind(), checkfirst=True)
    op.execute("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START WITH 1 INCREMENT BY 1;")

    op.add_column(
        'invoice_items',
        sa.Column(
            'item_type',
            _invoice_item_type_enum,
            server_default=sa.text("'base_subscription'"),
            nullable=False,
        ),
    )
    op.create_index(op.f('ix_invoice_items_item_type'), 'invoice_items', ['item_type'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_invoice_items_item_type'), table_name='invoice_items')
    op.drop_column('invoice_items', 'item_type')
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq;")
    _invoice_item_type_enum.drop(op.get_bind(), checkfirst=True)
