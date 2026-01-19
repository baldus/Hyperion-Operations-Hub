"""Set item location foreign keys to ON DELETE SET NULL.

Revision ID: 20250312_set_item_location_fks_ondelete
Revises: 20240918_add_item_locations
Create Date: 2025-03-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250312_set_item_location_fks_ondelete"
down_revision = "20240918_add_item_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS item_default_location_id_fkey")
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS fk_item_default_location")
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS item_secondary_location_id_fkey")
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS fk_item_secondary_location")
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS item_point_of_use_location_id_fkey")
    op.execute("ALTER TABLE item DROP CONSTRAINT IF EXISTS fk_item_point_of_use_location")

    op.alter_column("item", "default_location_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("item", "secondary_location_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "item", "point_of_use_location_id", existing_type=sa.Integer(), nullable=True
    )

    op.create_foreign_key(
        "fk_item_default_location",
        "item",
        "location",
        ["default_location_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_item_secondary_location",
        "item",
        "location",
        ["secondary_location_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_item_point_of_use_location",
        "item",
        "location",
        ["point_of_use_location_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_item_point_of_use_location", "item", type_="foreignkey")
    op.drop_constraint("fk_item_secondary_location", "item", type_="foreignkey")
    op.drop_constraint("fk_item_default_location", "item", type_="foreignkey")

    op.create_foreign_key(
        "fk_item_default_location",
        "item",
        "location",
        ["default_location_id"],
        ["id"],
    )
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
