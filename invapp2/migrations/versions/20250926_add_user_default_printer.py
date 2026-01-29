"""Add per-user default printer.

Revision ID: 20250926_add_user_default_printer
Revises: 20250507_add_physical_inventory_tables
Create Date: 2025-09-26 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250926_add_user_default_printer"
down_revision = "20250507_add_physical_inventory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "printer",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user",
        sa.Column("default_printer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_default_printer",
        "user",
        "printer",
        ["default_printer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column("printer", "enabled", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_user_default_printer", "user", type_="foreignkey")
    op.drop_column("user", "default_printer_id")
    op.drop_column("printer", "enabled")
