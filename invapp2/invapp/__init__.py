from datetime import date, timedelta

from flask import Flask, render_template
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import NoSuchTableError, OperationalError

from .extensions import db
from .routes import admin, inventory, reports, orders, work, settings, printers
from config import Config
from . import models  # ensure models are registered with SQLAlchemy


def _ensure_item_columns(engine):
    """Ensure legacy databases include the latest ``item`` columns."""

    inspector = inspect(engine)
    try:
        item_columns = {col["name"] for col in inspector.get_columns("item")}
    except (NoSuchTableError, OperationalError):
        item_columns = set()

    columns_to_add = []
    required_columns = {
        "type": "VARCHAR",
        "notes": "TEXT",
        "list_price": "NUMERIC(12, 2)",
        "last_unit_cost": "NUMERIC(12, 2)",
        "item_class": "VARCHAR",
    }

    for column_name, column_type in required_columns.items():
        if column_name not in item_columns:
            columns_to_add.append((column_name, column_type))

    if columns_to_add:
        with engine.begin() as conn:
            for column_name, column_type in columns_to_add:
                conn.execute(
                    text(f"ALTER TABLE item ADD COLUMN {column_name} {column_type}")
                )


def _ensure_order_schema(engine):
    """Make sure legacy databases pick up the expanded order schema."""
    inspector = inspect(engine)
    existing_tables = {table.lower() for table in inspector.get_table_names()}
    required_tables = {
        "order_item",
        "order_bom_component",
        "order_step",
        "order_step_component",
        "item_bom",
        "item_bom_component",
    }
    missing_tables = required_tables - existing_tables
    if missing_tables:
        metadata = db.Model.metadata
        for table_name in missing_tables:
            metadata.tables[table_name].create(bind=engine)

    try:
        order_columns = {col["name"] for col in inspector.get_columns("order")}
    except (NoSuchTableError, OperationalError):
        order_columns = set()

    columns_to_add = []
    if "customer_name" not in order_columns:
        columns_to_add.append(("customer_name", "VARCHAR"))
    if "created_by" not in order_columns:
        columns_to_add.append(("created_by", "VARCHAR"))
    if "general_notes" not in order_columns:
        columns_to_add.append(("general_notes", "TEXT"))

    if columns_to_add:
        with engine.begin() as conn:
            for column_name, column_type in columns_to_add:
                conn.execute(
                    text(
                        f"ALTER TABLE \"order\" ADD COLUMN {column_name} {column_type}"
                    )
                )


def create_app(config_override=None):
    app = Flask(__name__)

    # load configuration from environment variables
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    # ✅ init db with app
    db.init_app(app)
    # create tables if they do not exist and ensure legacy schema has "type"
    with app.app_context():
        db.create_all()
        _ensure_item_columns(db.engine)
        _ensure_order_schema(db.engine)

    # register blueprints
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)
    app.register_blueprint(admin.bp)

    @app.route("/")
    def home():
        today = date.today()
        due_soon_window = timedelta(days=3)
        soon_cutoff = today + due_soon_window
        active_statuses = tuple(models.OrderStatus.ACTIVE_STATES)

        due_soon_query = models.Order.query.filter(
            models.Order.status.in_(active_statuses),
            models.Order.promised_date.isnot(None),
            models.Order.promised_date >= today,
            models.Order.promised_date <= soon_cutoff,
        )
        due_soon_count = due_soon_query.count()
        due_soon_preview = (
            due_soon_query.order_by(
                models.Order.promised_date.asc(),
                models.Order.order_number.asc(),
            )
            .limit(5)
            .all()
        )

        overdue_query = models.Order.query.filter(
            models.Order.status.in_(active_statuses),
            models.Order.promised_date.isnot(None),
            models.Order.promised_date < today,
        )
        overdue_count = overdue_query.count()
        overdue_preview = (
            overdue_query.order_by(
                models.Order.promised_date.asc(),
                models.Order.order_number.asc(),
            )
            .limit(5)
            .all()
        )

        waiting_material_count = (
            models.Order.query.filter(
                models.Order.status == models.OrderStatus.WAITING_MATERIAL
            ).count()
        )

        movement_totals = (
            db.session.query(
                models.Movement.item_id,
                func.coalesce(func.sum(models.Movement.quantity), 0).label(
                    "on_hand"
                ),
            )
            .group_by(models.Movement.item_id)
            .all()
        )
        on_hand_map = {
            item_id: int(total or 0) for item_id, total in movement_totals
        }

        items = models.Item.query.order_by(models.Item.sku).all()

        low_items = []
        out_items = []
        for item in items:
            min_stock_raw = item.min_stock or 0
            try:
                min_stock = int(min_stock_raw)
            except (TypeError, ValueError):
                min_stock = 0
            if min_stock <= 0:
                continue
            on_hand = on_hand_map.get(item.id, 0)
            shortage = max(min_stock - on_hand, 0)
            entry = {
                "item": item,
                "on_hand": on_hand,
                "min_stock": min_stock,
                "shortage": shortage,
            }
            if on_hand <= 0:
                entry["is_out"] = True
                out_items.append(entry)
            elif on_hand < min_stock:
                entry["is_out"] = False
                low_items.append(entry)

        out_items.sort(key=lambda entry: (-entry["shortage"], entry["item"].sku))
        low_items.sort(key=lambda entry: (-entry["shortage"], entry["item"].sku))

        preview_limit = 5
        inventory_preview = (out_items + low_items)[:preview_limit]

        order_summary = {
            "due_soon_window_days": due_soon_window.days,
            "due_soon_count": due_soon_count,
            "due_soon_preview": due_soon_preview,
            "overdue_count": overdue_count,
            "overdue_preview": overdue_preview,
            "waiting_material_count": waiting_material_count,
            "preview_limit": 5,
        }

        inventory_summary = {
            "out_count": len(out_items),
            "low_count": len(low_items),
            "preview": inventory_preview,
            "preview_limit": preview_limit,
            "total_alerts": len(out_items) + len(low_items),
        }

        return render_template(
            "home.html",
            order_summary=order_summary,
            inventory_summary=inventory_summary,
        )

    return app
