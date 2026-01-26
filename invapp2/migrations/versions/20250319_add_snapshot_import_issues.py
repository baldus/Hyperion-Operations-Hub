"""Add snapshot import issues table and secondary match text.

Revision ID: 20250319_add_snapshot_import_issues
Revises: 20250318_add_snapshot_source_text_fields
Create Date: 2025-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250319_add_snapshot_import_issues"
down_revision = "20250318_add_snapshot_source_text_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_snapshot_line",
        sa.Column("source_secondary_match_text", sa.String(length=255), nullable=True),
    )
    op.create_table(
        "inventory_snapshot_import_issue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("inventory_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("primary_value", sa.String(length=255), nullable=True),
        sa.Column("secondary_value", sa.String(length=255), nullable=True),
        sa.Column("row_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("inventory_snapshot_import_issue")
    op.drop_column("inventory_snapshot_line", "source_secondary_match_text")
