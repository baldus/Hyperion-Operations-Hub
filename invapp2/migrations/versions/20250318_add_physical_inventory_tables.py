"""Add physical inventory snapshot tables.

Revision ID: 20250318_add_physical_inventory_tables
Revises: 20250312_set_item_location_fks_ondelete
Create Date: 2025-03-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250318_add_physical_inventory_tables"
down_revision = "20250312_set_item_location_fks_ondelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("snapshot_date", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "inventory_snapshot_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("system_total_qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("uom", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["inventory_snapshot.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
        sa.UniqueConstraint("snapshot_id", "item_id", name="uq_snapshot_item"),
    )

    op.create_table(
        "inventory_count_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("counted_qty", sa.Numeric(12, 3), nullable=True),
        sa.Column("counted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("counted_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["inventory_snapshot.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(
            ["counted_by_user_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "item_id",
            "location_id",
            name="uq_snapshot_item_location",
        ),
    )

    op.create_index(
        "ix_inventory_count_line_snapshot_location",
        "inventory_count_line",
        ["snapshot_id", "location_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_count_line_snapshot_location", table_name="inventory_count_line")
    op.drop_table("inventory_count_line")
    op.drop_table("inventory_snapshot_line")
    op.drop_table("inventory_snapshot")
