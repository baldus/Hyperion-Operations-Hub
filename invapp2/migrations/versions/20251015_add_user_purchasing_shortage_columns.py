"""Add per-user purchasing shortage column preferences.

Revision ID: 20251015_add_user_purchasing_shortage_columns
Revises: 20251010_add_shipped_from_supplier_date_to_purchase_request
Create Date: 2025-10-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251015_add_user_purchasing_shortage_columns"
down_revision = "20251010_add_shipped_from_supplier_date_to_purchase_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("purchasing_shortage_columns", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "purchasing_shortage_columns")
