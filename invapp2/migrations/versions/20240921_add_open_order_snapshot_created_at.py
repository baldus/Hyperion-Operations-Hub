"""Add created_at to open order snapshots.

Revision ID: 20240921_add_open_order_snapshot_created_at
Revises: 20240920_add_open_orders_tables
Create Date: 2024-09-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240921_add_open_order_snapshot_created_at"
down_revision = "20240920_add_open_orders_tables"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "open_order_line_snapshot", "created_at"):
        op.add_column(
            "open_order_line_snapshot",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    op.execute(
        sa.text(
            "UPDATE open_order_line_snapshot SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )
    )

    op.alter_column(
        "open_order_line_snapshot",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "open_order_line_snapshot", "created_at"):
        op.drop_column("open_order_line_snapshot", "created_at")
