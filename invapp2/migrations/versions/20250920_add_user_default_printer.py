"""Add default printer to user accounts.

Revision ID: 20250920_add_user_default_printer
Revises: 20250507_add_physical_inventory_tables
Create Date: 2025-09-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250920_add_user_default_printer"
down_revision = "20250507_add_physical_inventory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("default_printer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "user_default_printer_id_fkey",
        "user",
        "printer",
        ["default_printer_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("user_default_printer_id_fkey", "user", type_="foreignkey")
    op.drop_column("user", "default_printer_id")
