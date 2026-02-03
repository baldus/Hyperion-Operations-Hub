"""Add user purchasing shortage column preferences.

Revision ID: 20251016_add_user_purchasing_shortage_columns
Revises: 20251015_add_user_purchasing_shortage_columns
Create Date: 2025-10-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251016_add_user_purchasing_shortage_columns"
down_revision = "20251015_add_user_purchasing_shortage_columns"
branch_labels = None
depends_on = None


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(connection)
    columns = inspector.get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    connection = op.get_bind()
    if not _column_exists(connection, "user", "purchasing_shortage_columns"):
        op.add_column(
            "user",
            sa.Column("purchasing_shortage_columns", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    connection = op.get_bind()
    if _column_exists(connection, "user", "purchasing_shortage_columns"):
        op.drop_column("user", "purchasing_shortage_columns")
