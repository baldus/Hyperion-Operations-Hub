"""Add open order status, headers, and notes.

Revision ID: 20240922_add_open_order_status_and_notes
Revises: 20240921_add_open_order_snapshot_created_at
Create Date: 2024-09-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20240922_add_open_order_status_and_notes"
down_revision = "20240921_add_open_order_snapshot_created_at"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

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

    op.create_table(
        "open_order_note",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["open_order.id"],
            name="fk_open_order_note_order",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_open_order_note_user",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_open_order_note_order_id", "open_order_note", ["order_id"])

    op.create_table(
        "open_order_action_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["open_order.id"],
            name="fk_open_order_action_item_order",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_open_order_action_item_creator",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["done_by_user_id"],
            ["user.id"],
            name="fk_open_order_action_item_done_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_open_order_action_item_order_id",
        "open_order_action_item",
        ["order_id"],
    )
    op.create_index(
        "ix_open_order_action_item_is_done",
        "open_order_action_item",
        ["is_done"],
    )

    if not _has_column(inspector, "open_order_line", "order_id"):
        op.add_column(
            "open_order_line",
            sa.Column("order_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_open_order_line_order",
            "open_order_line",
            "open_order",
            ["order_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _has_column(inspector, "open_order_line", "status"):
        op.add_column(
            "open_order_line",
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="open",
            ),
        )

    if not _has_column(inspector, "open_order_line", "completed_by_user_id"):
        op.add_column(
            "open_order_line",
            sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_open_order_line_completed_by",
            "open_order_line",
            "user",
            ["completed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        sa.text(
            "UPDATE open_order_line SET status = 'open' WHERE status IS NULL"
        )
    )

    op.create_index("ix_open_order_line_status", "open_order_line", ["status"])
    op.create_index("ix_open_order_line_completed_at", "open_order_line", ["completed_at"])

    op.alter_column(
        "open_order_line",
        "completed_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    op.drop_index("ix_open_order_line_completed_at", table_name="open_order_line")
    op.drop_index("ix_open_order_line_status", table_name="open_order_line")

    op.drop_constraint("fk_open_order_line_completed_by", "open_order_line", type_="foreignkey")
    op.drop_constraint("fk_open_order_line_order", "open_order_line", type_="foreignkey")

    op.drop_column("open_order_line", "completed_by_user_id")
    op.drop_column("open_order_line", "status")
    op.drop_column("open_order_line", "order_id")

    op.drop_index("ix_open_order_action_item_is_done", table_name="open_order_action_item")
    op.drop_index("ix_open_order_action_item_order_id", table_name="open_order_action_item")
    op.drop_table("open_order_action_item")

    op.drop_index("ix_open_order_note_order_id", table_name="open_order_note")
    op.drop_table("open_order_note")

    op.drop_index("ix_open_order_so_no", table_name="open_order")
    op.drop_table("open_order")
