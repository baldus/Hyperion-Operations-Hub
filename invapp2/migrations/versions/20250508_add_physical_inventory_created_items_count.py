"""Add created_items_count to physical inventory snapshot.

Revision ID: 20250508_add_physical_inventory_created_items_count
Revises: 20250507_add_physical_inventory_tables
Create Date: 2025-05-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250508_add_physical_inventory_created_items_count"
down_revision = "20250507_add_physical_inventory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "physical_inventory_snapshot",
        sa.Column("created_items_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column(
        "physical_inventory_snapshot",
        "created_items_count",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("physical_inventory_snapshot", "created_items_count")
