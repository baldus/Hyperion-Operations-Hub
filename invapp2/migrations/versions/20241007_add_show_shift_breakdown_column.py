"""Add show_shift_breakdown column to production_chart_settings"""

from alembic import op
import sqlalchemy as sa


revision = "20241007_add_show_shift_breakdown"
down_revision = "20240930_align_schema"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    table_name = "production_chart_settings"
    column_name = "show_shift_breakdown"

    if _column_exists(table_name, column_name):
        return

    op.add_column(
        table_name,
        sa.Column(
            column_name,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.alter_column(
        table_name,
        column_name,
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    table_name = "production_chart_settings"
    column_name = "show_shift_breakdown"

    if not _column_exists(table_name, column_name):
        return

    op.drop_column(table_name, column_name)
