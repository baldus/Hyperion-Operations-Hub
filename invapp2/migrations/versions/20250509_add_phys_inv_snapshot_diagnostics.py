"""Add physical inventory snapshot diagnostics fields.

Revision ID: 20250509_add_phys_inv_snapshot_diagnostics
Revises: 20250508_add_physical_inventory_created_items_count
Create Date: 2025-05-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20250509_add_phys_inv_snapshot_diagnostics"
down_revision = "20250508_add_physical_inventory_created_items_count"
branch_labels = None
depends_on = None


def _missing_columns(columns: set[str], names: list[str]) -> list[str]:
    return [name for name in names if name not in columns]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("physical_inventory_snapshot")}

    with op.batch_alter_table("physical_inventory_snapshot") as batch:
        if "created_items_count" not in columns:
            batch.add_column(
                sa.Column("created_items_count", sa.Integer(), nullable=False, server_default="0")
            )
        if "unmatched_details" not in columns:
            batch.add_column(
                sa.Column(
                    "unmatched_details",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=False,
                    server_default=sa.text("'[]'::jsonb"),
                )
            )
        if "ambiguous_details" not in columns:
            batch.add_column(
                sa.Column(
                    "ambiguous_details",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=False,
                    server_default=sa.text("'[]'::jsonb"),
                )
            )

    columns = {column["name"] for column in inspector.get_columns("physical_inventory_snapshot")}
    missing = _missing_columns(
        columns,
        ["created_items_count", "unmatched_details", "ambiguous_details"],
    )
    if not missing:
        with op.batch_alter_table("physical_inventory_snapshot") as batch:
            if "created_items_count" in columns:
                batch.alter_column("created_items_count", server_default=None)
            if "unmatched_details" in columns:
                batch.alter_column("unmatched_details", server_default=None)
            if "ambiguous_details" in columns:
                batch.alter_column("ambiguous_details", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("physical_inventory_snapshot")}

    with op.batch_alter_table("physical_inventory_snapshot") as batch:
        if "ambiguous_details" in columns:
            batch.drop_column("ambiguous_details")
        if "unmatched_details" in columns:
            batch.drop_column("unmatched_details")
        if "created_items_count" in columns:
            batch.drop_column("created_items_count")
