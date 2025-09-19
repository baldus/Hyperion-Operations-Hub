from datetime import datetime, timedelta

from flask import Flask, render_template, url_for
from sqlalchemy import inspect, text, func
from sqlalchemy.exc import NoSuchTableError, OperationalError


from .extensions import db, login_manager
from .routes import (
    admin,
    auth as auth_routes,
    inventory,
    orders,
    printers,
    reports,
    settings,
    work,

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
        "bill_of_material",
        "bill_of_material_component",
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
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"
    # create tables if they do not exist and ensure legacy schema has "type"
    with app.app_context():
        db.create_all()
        _ensure_item_columns(db.engine)
        _ensure_order_schema(db.engine)

    # register blueprints
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)
    app.register_blueprint(admin.bp)

    @app.route("/")
    def home():
        order_counts = {status: 0 for status in OrderStatus.ALL_STATUSES}
        order_rows = (
            db.session.query(Order.status, func.count(Order.id))
            .group_by(Order.status)
            .all()
        )
        for status, count in order_rows:
            order_counts[status] = int(count or 0)

        active_pipeline = sum(order_counts[status] for status in OrderStatus.ACTIVE_STATES)
        closed_count = order_counts[OrderStatus.CLOSED]
        cancelled_count = order_counts[OrderStatus.CANCELLED]

        stock_summary = get_inventory_stock_alerts()
        low_stock_count = len(stock_summary["low_stock_items"])
        near_stock_count = len(stock_summary["near_stock_items"])

        now = datetime.utcnow()
        movement_cutoff = now - timedelta(days=7)
        batch_cutoff = now - timedelta(days=30)

        recent_movement_count = (
            db.session.query(func.count(Movement.id))
            .filter(Movement.date >= movement_cutoff)
            .scalar()
        ) or 0
        recent_batch_count = (
            db.session.query(func.count(Batch.id))
            .filter(Batch.received_date >= batch_cutoff)
            .scalar()
        ) or 0

        stat_cards = [
            {
                "title": "Order Pipeline",
                "value": active_pipeline,
                "url": url_for("orders.orders_home"),
                "description": (
                    f"Scheduled: {order_counts[OrderStatus.SCHEDULED]} • "
                    f"Open: {order_counts[OrderStatus.OPEN]} • "
                    f"Waiting: {order_counts[OrderStatus.WAITING_MATERIAL]}"
                ),
            },
            {
                "title": "Completed / Cancelled",
                "value": closed_count + cancelled_count,
                "url": url_for("orders.orders_home"),
                "description": (
                    f"Closed: {closed_count} • Cancelled: {cancelled_count}"
                ),
            },
            {
                "title": "Low Stock Alerts",
                "value": low_stock_count,
                "url": url_for("inventory.inventory_home"),
                "description": "Items below 105% of minimum levels.",
            },
            {
                "title": "Near Minimum Alerts",
                "value": near_stock_count,
                "url": url_for("inventory.inventory_home"),
                "description": "Items within 125% of minimum levels.",
            },
            {
                "title": "Movements (7 days)",
                "value": int(recent_movement_count),
                "url": url_for("inventory.cycle_count_home"),
                "description": "Inventory transactions logged this week.",
            },
            {
                "title": "Batches Received (30 days)",
                "value": int(recent_batch_count),
                "url": url_for("inventory.inventory_home"),
                "description": "New lots entered in the last month.",
            },
        ]

        return render_template(
            "home.html",
            stat_cards=stat_cards,
        )

    return app


@login_manager.user_loader
def load_user(user_id):
    try:
        return models.User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None
