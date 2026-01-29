"""Add removed_at to location.

Revision ID: 20251007_add_location_removed_at
Revises: 20250926_add_user_default_printer
Create Date: 2025-10-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251007_add_location_removed_at"
down_revision = "20250926_add_user_default_printer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("location", sa.Column("removed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_location_removed_at", "location", ["removed_at"])


def downgrade() -> None:
    op.drop_index("ix_location_removed_at", table_name="location")
    op.drop_column("location", "removed_at")
