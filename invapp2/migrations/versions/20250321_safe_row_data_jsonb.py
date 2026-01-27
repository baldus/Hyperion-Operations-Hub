"""Safely migrate import issue row_data to JSONB.

Revision ID: 20250321_safe_row_data_jsonb
Revises: 20250320_update_snapshot_import_issue_columns
Create Date: 2025-03-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20250321_safe_row_data_jsonb"
down_revision = "20250320_update_snapshot_import_issue_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "inventory_snapshot_import_issue",
            sa.Column("row_data_json", postgresql.JSONB(), nullable=True),
        )
        op.execute(
            """
            DO $$
            DECLARE
                row_record RECORD;
            BEGIN
                FOR row_record IN
                    SELECT id, row_data::text AS row_data_text
                    FROM inventory_snapshot_import_issue
                LOOP
                    BEGIN
                        UPDATE inventory_snapshot_import_issue
                        SET row_data_json = row_record.row_data_text::jsonb
                        WHERE id = row_record.id;
                    EXCEPTION WHEN others THEN
                        UPDATE inventory_snapshot_import_issue
                        SET row_data_json = jsonb_build_object('raw', row_record.row_data_text)
                        WHERE id = row_record.id;
                    END;
                END LOOP;
            END
            $$;
            """
        )
        op.drop_column("inventory_snapshot_import_issue", "row_data")
        op.alter_column(
            "inventory_snapshot_import_issue",
            "row_data_json",
            new_column_name="row_data",
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
        op.add_column(
            "inventory_snapshot_import_issue",
            sa.Column("row_data_text", sa.Text(), nullable=True),
        )
        op.execute(
            """
            UPDATE inventory_snapshot_import_issue
            SET row_data_text = row_data::text
            """
        )
        op.drop_column("inventory_snapshot_import_issue", "row_data")
        op.alter_column(
            "inventory_snapshot_import_issue",
            "row_data_text",
            new_column_name="row_data",
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
