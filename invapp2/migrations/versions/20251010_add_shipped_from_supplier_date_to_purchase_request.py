"""Add shipped from supplier date to purchase requests.

Revision ID: 20251010_add_shipped_from_supplier_date_to_purchase_request
Revises: 20250926_add_user_default_printer
Create Date: 2025-10-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251010_add_shipped_from_supplier_date_to_purchase_request"
down_revision = "20250926_add_user_default_printer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_request",
        sa.Column("shipped_from_supplier_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_request", "shipped_from_supplier_date")
