"""Widen snapshot import issue fields and use JSONB.

Revision ID: 20250320_update_snapshot_import_issue_columns
Revises: 20250319_add_snapshot_import_issues
Create Date: 2025-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20250320_update_snapshot_import_issue_columns"
down_revision = "20250319_add_snapshot_import_issues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE inventory_snapshot_import_issue "
            "ALTER COLUMN row_data TYPE jsonb USING row_data::jsonb"
        )
    else:
        op.alter_column(
            "inventory_snapshot_import_issue",
            "row_data",
            type_=sa.JSON(),
        )
    op.alter_column(
        "inventory_snapshot_import_issue",
        "primary_value",
        type_=sa.Text(),
        existing_type=sa.String(length=255),
    )
    op.alter_column(
        "inventory_snapshot_import_issue",
        "secondary_value",
        type_=sa.Text(),
        existing_type=sa.String(length=255),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE inventory_snapshot_import_issue "
            "ALTER COLUMN row_data TYPE json USING row_data::json"
        )
    else:
        op.alter_column(
            "inventory_snapshot_import_issue",
            "row_data",
            type_=sa.JSON(),
        )
    op.alter_column(
        "inventory_snapshot_import_issue",
        "secondary_value",
        type_=sa.String(length=255),
        existing_type=sa.Text(),
    )
    op.alter_column(
        "inventory_snapshot_import_issue",
        "primary_value",
        type_=sa.String(length=255),
        existing_type=sa.Text(),
    )
