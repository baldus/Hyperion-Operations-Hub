"""Add secondary and point-of-use locations to items.

Revision ID: 20240918_add_item_locations
Revises: 
Create Date: 2024-09-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240918_add_item_locations"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("item", sa.Column("secondary_location_id", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("point_of_use_location_id", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_item_secondary_location",
        "item",
        "location",
        ["secondary_location_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_item_point_of_use_location",
        "item",
        "location",
        ["point_of_use_location_id"],
        ["id"],
    )

    op.create_index(
        "ix_item_secondary_location_id",
        "item",
        ["secondary_location_id"],
    )
    op.create_index(
        "ix_item_point_of_use_location_id",
        "item",
        ["point_of_use_location_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_item_point_of_use_location_id", table_name="item")
    op.drop_index("ix_item_secondary_location_id", table_name="item")

    op.drop_constraint("fk_item_point_of_use_location", "item", type_="foreignkey")
    op.drop_constraint("fk_item_secondary_location", "item", type_="foreignkey")

    op.drop_column("item", "point_of_use_location_id")
    op.drop_column("item", "secondary_location_id")
