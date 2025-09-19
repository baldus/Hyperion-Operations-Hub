from flask import Flask, render_template
from sqlalchemy import inspect, text
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
        return render_template("home.html")

    return app
