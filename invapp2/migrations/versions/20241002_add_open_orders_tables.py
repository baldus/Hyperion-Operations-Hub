"""Add open orders import tables.

Revision ID: 20241002_add_open_orders_tables
Revises: 20240918_add_item_locations
Create Date: 2024-10-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20241002_add_open_orders_tables"
down_revision = "20240918_add_item_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "open_order_upload",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_open_order_upload_uploaded_at",
        "open_order_upload",
        ["uploaded_at"],
    )

    op.create_table(
        "open_order_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("natural_key", sa.String(length=128), nullable=False),
        sa.Column("so_no", sa.String(length=64), nullable=False),
        sa.Column("so_state", sa.String(length=64), nullable=True),
        sa.Column("so_date", sa.Date(), nullable=True),
        sa.Column("ship_by", sa.Date(), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("item_id", sa.String(length=128), nullable=True),
        sa.Column("line_description", sa.Text(), nullable=True),
        sa.Column("uom", sa.String(length=64), nullable=True),
        sa.Column("qty_ordered", sa.Numeric(12, 3), nullable=True),
        sa.Column("qty_shipped", sa.Numeric(12, 3), nullable=True),
        sa.Column("qty_remaining", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("part_number", sa.String(length=128), nullable=True),
        sa.Column("system_state", sa.String(length=32), nullable=False),
        sa.Column("first_seen_upload_id", sa.Integer(), nullable=True),
        sa.Column("last_seen_upload_id", sa.Integer(), nullable=True),
        sa.Column("completed_upload_id", sa.Integer(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("internal_status", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("promised_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["completed_upload_id"],
            ["open_order_upload.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_upload_id"],
            ["open_order_upload.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_upload_id"],
            ["open_order_upload.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("natural_key", name="uq_open_order_line_natural_key"),
    )
    op.create_index(
        "ix_open_order_line_natural_key",
        "open_order_line",
        ["natural_key"],
    )
    op.create_index(
        "ix_open_order_line_system_state",
        "open_order_line",
        ["system_state"],
    )
    op.create_index(
        "ix_open_order_line_customer_id",
        "open_order_line",
        ["customer_id"],
    )
    op.create_index(
        "ix_open_order_line_so_no",
        "open_order_line",
        ["so_no"],
    )
    op.create_index(
        "ix_open_order_line_item_id",
        "open_order_line",
        ["item_id"],
    )

    op.create_table(
        "open_order_line_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("upload_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["open_order_line.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["open_order_upload.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_open_order_line_snapshot_upload_id",
        "open_order_line_snapshot",
        ["upload_id"],
    )
    op.create_index(
        "ix_open_order_line_snapshot_line_id",
        "open_order_line_snapshot",
        ["line_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_open_order_line_snapshot_line_id", table_name="open_order_line_snapshot")
    op.drop_index("ix_open_order_line_snapshot_upload_id", table_name="open_order_line_snapshot")
    op.drop_table("open_order_line_snapshot")

    op.drop_index("ix_open_order_line_item_id", table_name="open_order_line")
    op.drop_index("ix_open_order_line_so_no", table_name="open_order_line")
    op.drop_index("ix_open_order_line_customer_id", table_name="open_order_line")
    op.drop_index("ix_open_order_line_system_state", table_name="open_order_line")
    op.drop_index("ix_open_order_line_natural_key", table_name="open_order_line")
    op.drop_table("open_order_line")

    op.drop_index("ix_open_order_upload_uploaded_at", table_name="open_order_upload")
    op.drop_table("open_order_upload")
