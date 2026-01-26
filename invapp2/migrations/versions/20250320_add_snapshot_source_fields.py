"""Add source part/description fields to inventory snapshot lines.

Revision ID: 20250320_add_snapshot_source_fields
Revises: 20250318_add_physical_inventory_tables
Create Date: 2025-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250320_add_snapshot_source_fields"
down_revision = "20250318_add_physical_inventory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_snapshot_line",
        sa.Column("source_part_number_text", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "inventory_snapshot_line",
        sa.Column("source_description_text", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_snapshot_line", "source_description_text")
    op.drop_column("inventory_snapshot_line", "source_part_number_text")
