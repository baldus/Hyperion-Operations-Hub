"""Backfill open_order_line.order_id.

Revision ID: 20240923_backfill_open_order_line_order_id
Revises: 20240922_add_open_order_status_and_notes
Create Date: 2024-09-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240923_backfill_open_order_line_order_id"
down_revision = "20240922_add_open_order_status_and_notes"
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def _foreign_key_exists(inspector, table_name: str, fk_name: str) -> bool:
    return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "open_order"):
        op.create_table(
            "open_order",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("so_no", sa.String(length=64), nullable=False),
            sa.Column("customer_id", sa.String(length=64), nullable=True),
            sa.Column("customer_name", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("so_no", "customer_id", name="uq_open_order_so_customer"),
        )
        op.create_index("ix_open_order_so_no", "open_order", ["so_no"])

    if not _column_exists(inspector, "open_order_line", "order_id"):
        op.add_column(
            "open_order_line",
            sa.Column("order_id", sa.Integer(), nullable=True),
        )

    if not _foreign_key_exists(inspector, "open_order_line", "fk_open_order_line_order"):
        op.create_foreign_key(
            "fk_open_order_line_order",
            "open_order_line",
            "open_order",
            ["order_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _index_exists(inspector, "open_order_line", "ix_open_order_line_order_id"):
        op.create_index(
            "ix_open_order_line_order_id",
            "open_order_line",
            ["order_id"],
        )

    op.execute(
        sa.text(
            """
            INSERT INTO open_order (so_no, customer_id, customer_name, created_at, updated_at)
            SELECT DISTINCT l.so_no, l.customer_id, l.customer_name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM open_order_line l
            WHERE l.so_no IS NOT NULL
            ON CONFLICT (so_no, customer_id) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE open_order_line l
            SET order_id = o.id
            FROM open_order o
            WHERE l.order_id IS NULL
              AND l.so_no = o.so_no
              AND (l.customer_id IS NOT DISTINCT FROM o.customer_id)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "open_order_line", "ix_open_order_line_order_id"):
        op.drop_index("ix_open_order_line_order_id", table_name="open_order_line")

    if _foreign_key_exists(inspector, "open_order_line", "fk_open_order_line_order"):
        op.drop_constraint("fk_open_order_line_order", "open_order_line", type_="foreignkey")

    if _column_exists(inspector, "open_order_line", "order_id"):
        op.drop_column("open_order_line", "order_id")
