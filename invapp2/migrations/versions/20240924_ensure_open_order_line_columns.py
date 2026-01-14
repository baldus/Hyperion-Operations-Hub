"""Ensure open order line columns exist.

Revision ID: 20240924_ensure_open_order_line_columns
Revises: 20240923_backfill_open_order_line_order_id
Create Date: 2024-09-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240924_ensure_open_order_line_columns"
down_revision = "20240923_backfill_open_order_line_order_id"
branch_labels = None
depends_on = None


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def _foreign_key_exists(inspector, table_name: str, fk_name: str) -> bool:
    return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table_name))


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "open_order_line", "status"):
        op.add_column(
            "open_order_line",
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="open",
            ),
        )

    if _column_exists(inspector, "open_order_line", "status"):
        op.execute(
            sa.text("UPDATE open_order_line SET status = 'open' WHERE status IS NULL")
        )

    if not _column_exists(inspector, "open_order_line", "completed_at"):
        op.add_column(
            "open_order_line",
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        op.alter_column(
            "open_order_line",
            "completed_at",
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            nullable=True,
        )

    if not _column_exists(inspector, "open_order_line", "completed_by_user_id"):
        op.add_column(
            "open_order_line",
            sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        )

    if not _foreign_key_exists(inspector, "open_order_line", "fk_open_order_line_completed_by"):
        op.create_foreign_key(
            "fk_open_order_line_completed_by",
            "open_order_line",
            "user",
            ["completed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _column_exists(inspector, "open_order_line", "order_id"):
        op.add_column(
            "open_order_line",
            sa.Column("order_id", sa.Integer(), nullable=True),
        )

    if _table_exists(inspector, "open_order") and not _foreign_key_exists(
        inspector, "open_order_line", "fk_open_order_line_order"
    ):
        op.create_foreign_key(
            "fk_open_order_line_order",
            "open_order_line",
            "open_order",
            ["order_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _index_exists(inspector, "open_order_line", "ix_open_order_line_last_seen_upload_id"):
        op.create_index(
            "ix_open_order_line_last_seen_upload_id",
            "open_order_line",
            ["last_seen_upload_id"],
        )

    if not _index_exists(inspector, "open_order_line", "ix_open_order_line_status"):
        op.create_index(
            "ix_open_order_line_status",
            "open_order_line",
            ["status"],
        )

    if not _index_exists(inspector, "open_order_line", "ix_open_order_line_completed_at"):
        op.create_index(
            "ix_open_order_line_completed_at",
            "open_order_line",
            ["completed_at"],
        )

    if not _index_exists(inspector, "open_order_line", "ix_open_order_line_order_id"):
        op.create_index(
            "ix_open_order_line_order_id",
            "open_order_line",
            ["order_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "open_order_line", "ix_open_order_line_order_id"):
        op.drop_index("ix_open_order_line_order_id", table_name="open_order_line")
    if _index_exists(inspector, "open_order_line", "ix_open_order_line_completed_at"):
        op.drop_index("ix_open_order_line_completed_at", table_name="open_order_line")
    if _index_exists(inspector, "open_order_line", "ix_open_order_line_status"):
        op.drop_index("ix_open_order_line_status", table_name="open_order_line")
    if _index_exists(inspector, "open_order_line", "ix_open_order_line_last_seen_upload_id"):
        op.drop_index(
            "ix_open_order_line_last_seen_upload_id",
            table_name="open_order_line",
        )

    if _foreign_key_exists(inspector, "open_order_line", "fk_open_order_line_order"):
        op.drop_constraint("fk_open_order_line_order", "open_order_line", type_="foreignkey")
    if _foreign_key_exists(inspector, "open_order_line", "fk_open_order_line_completed_by"):
        op.drop_constraint(
            "fk_open_order_line_completed_by",
            "open_order_line",
            type_="foreignkey",
        )

    if _column_exists(inspector, "open_order_line", "order_id"):
        op.drop_column("open_order_line", "order_id")
    if _column_exists(inspector, "open_order_line", "completed_by_user_id"):
        op.drop_column("open_order_line", "completed_by_user_id")
    if _column_exists(inspector, "open_order_line", "completed_at"):
        op.drop_column("open_order_line", "completed_at")
    if _column_exists(inspector, "open_order_line", "status"):
        op.drop_column("open_order_line", "status")
