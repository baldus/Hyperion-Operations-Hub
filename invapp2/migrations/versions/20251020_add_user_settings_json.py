"""Add per-user settings JSON.

Revision ID: 20251020_add_user_settings_json
Revises: 20251010_add_shipped_from_supplier_date_to_purchase_request
Create Date: 2025-10-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251020_add_user_settings_json"
down_revision = "20251010_add_shipped_from_supplier_date_to_purchase_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("user_settings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "user_settings")
