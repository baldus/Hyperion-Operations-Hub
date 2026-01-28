"""Add physical inventory snapshot tables.

Revision ID: 20250507_add_physical_inventory_tables
Revises: 20250312_set_item_location_fks_ondelete
Create Date: 2025-05-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250507_add_physical_inventory_tables"
down_revision = "20250312_set_item_location_fks_ondelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physical_inventory_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("primary_upload_column", sa.String(length=255), nullable=False),
        sa.Column("primary_item_field", sa.String(length=255), nullable=False),
        sa.Column("secondary_upload_column", sa.String(length=255), nullable=True),
        sa.Column("secondary_item_field", sa.String(length=255), nullable=True),
        sa.Column("quantity_column", sa.String(length=255), nullable=False),
        sa.Column("normalization_options", sa.JSON(), nullable=False),
        sa.Column("duplicate_strategy", sa.String(length=32), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("matched_rows", sa.Integer(), nullable=False),
        sa.Column("unmatched_rows", sa.Integer(), nullable=False),
        sa.Column("ambiguous_rows", sa.Integer(), nullable=False),
        sa.Column("unmatched_details", sa.JSON(), nullable=True),
        sa.Column("ambiguous_details", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "physical_inventory_snapshot_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("erp_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(12, 3), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["physical_inventory_snapshot.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
    )


def downgrade() -> None:
    op.drop_table("physical_inventory_snapshot_line")
    op.drop_table("physical_inventory_snapshot")
