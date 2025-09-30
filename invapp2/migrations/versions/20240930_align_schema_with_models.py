"""Align legacy schema with SQLAlchemy models"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20240930_align_schema"
down_revision = None
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(table)}
    if column.name in existing_columns:
        return
    op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing("item", sa.Column("type", sa.String(), nullable=True))
    _add_column_if_missing("item", sa.Column("notes", sa.Text(), nullable=True))
    _add_column_if_missing(
        "item",
        sa.Column("list_price", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    _add_column_if_missing(
        "item",
        sa.Column("last_unit_cost", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    _add_column_if_missing("item", sa.Column("item_class", sa.String(), nullable=True))

    _add_column_if_missing("batch", sa.Column("expiration_date", sa.Date(), nullable=True))
    _add_column_if_missing("batch", sa.Column("supplier_name", sa.String(), nullable=True))
    _add_column_if_missing("batch", sa.Column("supplier_code", sa.String(), nullable=True))
    _add_column_if_missing("batch", sa.Column("purchase_order", sa.String(), nullable=True))
    _add_column_if_missing("batch", sa.Column("notes", sa.Text(), nullable=True))

    _add_column_if_missing("order", sa.Column("customer_name", sa.String(), nullable=True))
    _add_column_if_missing("order", sa.Column("created_by", sa.String(), nullable=True))
    _add_column_if_missing("order", sa.Column("general_notes", sa.Text(), nullable=True))

    _add_column_if_missing(
        "production_daily_record",
        sa.Column(
            "gates_employees",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column(
            "gates_hours_ot",
            sa.Numeric(precision=7, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column(
            "additional_employees",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column(
            "additional_hours_ot",
            sa.Numeric(precision=7, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column("shift_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column("product_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column("scrap_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "production_daily_record",
        sa.Column(
            "downtime_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    day_of_week_info = next(
        (col for col in inspector.get_columns("production_daily_record") if col["name"] == "day_of_week"),
        None,
    )
    if day_of_week_info is not None:
        current_type = day_of_week_info.get("type")
        current_length = getattr(current_type, "length", None)
        if current_length is not None and current_length < 32:
            op.alter_column(
                "production_daily_record",
                "day_of_week",
                existing_type=sa.String(length=current_length),
                type_=sa.String(length=32),
                existing_nullable=day_of_week_info.get("nullable", True),
            )

    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_trendline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_output_per_hour",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_shift_breakdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_product_type_breakdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_scrap_trend",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_downtime_analysis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "show_cumulative_goal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )
    _add_column_if_missing(
        "production_chart_settings",
        sa.Column(
            "custom_builder_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.alter_column(
        "production_daily_record",
        "gates_employees",
        server_default=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_daily_record",
        "gates_hours_ot",
        server_default=None,
        existing_type=sa.Numeric(precision=7, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "production_daily_record",
        "additional_employees",
        server_default=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_daily_record",
        "additional_hours_ot",
        server_default=None,
        existing_type=sa.Numeric(precision=7, scale=2),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_trendline",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_output_per_hour",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_shift_breakdown",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_product_type_breakdown",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_scrap_trend",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_downtime_analysis",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "production_chart_settings",
        "show_cumulative_goal",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("production_chart_settings", "custom_builder_state")
    op.drop_column("production_chart_settings", "show_cumulative_goal")
    op.drop_column("production_chart_settings", "show_downtime_analysis")
    op.drop_column("production_chart_settings", "show_scrap_trend")
    op.drop_column("production_chart_settings", "show_product_type_breakdown")
    op.drop_column("production_chart_settings", "show_shift_breakdown")
    op.drop_column("production_chart_settings", "show_output_per_hour")
    op.drop_column("production_chart_settings", "show_trendline")

    op.alter_column(
        "production_daily_record",
        "day_of_week",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_column("production_daily_record", "downtime_summary")
    op.drop_column("production_daily_record", "scrap_summary")
    op.drop_column("production_daily_record", "product_mix")
    op.drop_column("production_daily_record", "shift_summary")
    op.drop_column("production_daily_record", "additional_hours_ot")
    op.drop_column("production_daily_record", "additional_employees")
    op.drop_column("production_daily_record", "gates_hours_ot")
    op.drop_column("production_daily_record", "gates_employees")

    op.drop_column("order", "general_notes")
    op.drop_column("order", "created_by")
    op.drop_column("order", "customer_name")

    op.drop_column("batch", "notes")
    op.drop_column("batch", "purchase_order")
    op.drop_column("batch", "supplier_code")
    op.drop_column("batch", "supplier_name")
    op.drop_column("batch", "expiration_date")

    op.drop_column("item", "item_class")
    op.drop_column("item", "last_unit_cost")
    op.drop_column("item", "list_price")
    op.drop_column("item", "notes")
    op.drop_column("item", "type")
