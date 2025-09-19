from datetime import date, timedelta

from flask import Flask, render_template
from sqlalchemy import and_, func, inspect, or_, text
from sqlalchemy.exc import NoSuchTableError, OperationalError
from sqlalchemy.orm import joinedload

from .extensions import db, login_manager
from .routes import admin, auth, inventory, reports, orders, work, settings, printers
from config import Config
from . import models  # ensure models are registered with SQLAlchemy
from .models import Item, Movement, Order, OrderLine, OrderStatus, User


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


def _ensure_bom_schema(engine):
    """Ensure master bill of material tables are available in legacy databases."""

    inspector = inspect(engine)
    existing_tables = {table.lower() for table in inspector.get_table_names()}
    required_tables = {"bill_of_material", "bill_of_material_component"}
    missing_tables = required_tables - existing_tables
    if missing_tables:
        metadata = db.Model.metadata
        for table_name in missing_tables:
            metadata.tables[table_name].create(bind=engine)


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
    # create tables if they do not exist and ensure legacy schema has "type"
    with app.app_context():
        db.create_all()
        _ensure_item_columns(db.engine)
        _ensure_order_schema(db.engine)
        _ensure_bom_schema(db.engine)

    # register blueprints
    app.register_blueprint(inventory.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)
    app.register_blueprint(admin.bp)

    @app.route("/")
    def home():
        today = date.today()
        soon_threshold = today + timedelta(days=7)

        active_filter = Order.status.in_(OrderStatus.ACTIVE_STATES)
        due_soon_filter = or_(
            and_(
                Order.promised_date.isnot(None),
                Order.promised_date >= today,
                Order.promised_date <= soon_threshold,
            ),
            and_(
                Order.scheduled_completion_date.isnot(None),
                Order.scheduled_completion_date >= today,
                Order.scheduled_completion_date <= soon_threshold,
            ),
        )

        overdue_filter = or_(
            and_(Order.promised_date.isnot(None), Order.promised_date < today),
            and_(
                Order.scheduled_completion_date.isnot(None),
                Order.scheduled_completion_date < today,
            ),
        )

        due_soon_query = Order.query.filter(active_filter).filter(due_soon_filter)
        overdue_query = Order.query.filter(active_filter).filter(overdue_filter)

        waiting_material_count = (
            Order.query.filter(Order.status == OrderStatus.WAITING_MATERIAL).count()
        )

        due_soon_orders = (
            due_soon_query.options(
                joinedload(Order.order_lines).joinedload(OrderLine.item)
            )
            .order_by(
                Order.promised_date.is_(None),
                Order.promised_date,
                Order.scheduled_completion_date.is_(None),
                Order.scheduled_completion_date,
            )
            .limit(5)
            .all()
        )
        overdue_orders = (
            overdue_query.options(
                joinedload(Order.order_lines).joinedload(OrderLine.item)
            )
            .order_by(
                Order.promised_date.is_(None),
                Order.promised_date,
                Order.scheduled_completion_date.is_(None),
                Order.scheduled_completion_date,
            )
            .limit(5)
            .all()
        )

        due_soon_total = due_soon_query.count()
        overdue_total = overdue_query.count()

        movement_totals = (
            db.session.query(
                Movement.item_id,
                func.coalesce(func.sum(Movement.quantity), 0).label("total"),
            )
            .group_by(Movement.item_id)
            .all()
        )
        on_hand_map = {item_id: int(total or 0) for item_id, total in movement_totals}

        items_with_minimum = (
            Item.query.filter(Item.min_stock.isnot(None))
            .filter(Item.min_stock > 0)
            .order_by(Item.sku)
            .all()
        )

        low_stock_items = []
        out_of_stock_items = []
        for item in items_with_minimum:
            on_hand = on_hand_map.get(item.id, 0)
            minimum = int(item.min_stock or 0)
            if on_hand <= 0:
                out_of_stock_items.append(
                    {"item": item, "on_hand": on_hand, "min_stock": minimum}
                )
            elif on_hand < minimum:
                coverage = (
                    on_hand / float(item.min_stock)
                    if item.min_stock not in (None, 0)
                    else None
                )
                low_stock_items.append(
                    {
                        "item": item,
                        "on_hand": on_hand,
                        "min_stock": minimum,
                        "coverage": coverage,
                    }
                )

        low_stock_items.sort(key=lambda entry: entry.get("coverage") or 0)
        out_of_stock_items.sort(key=lambda entry: entry["item"].sku)

        inventory_summary = {
            "low": len(low_stock_items),
            "out": len(out_of_stock_items),
            "total_tracked": len(items_with_minimum),
        }

        active_orders_total = Order.query.filter(active_filter).count()

        return render_template(
            "home.html",
            active_orders_total=active_orders_total,
            due_soon_orders=due_soon_orders,
            due_soon_total=due_soon_total,
            overdue_orders=overdue_orders,
            overdue_total=overdue_total,
            waiting_material_count=waiting_material_count,
            low_stock_items=low_stock_items[:5],
            out_of_stock_items=out_of_stock_items[:5],
            inventory_summary=inventory_summary,
        )

    return app


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None
