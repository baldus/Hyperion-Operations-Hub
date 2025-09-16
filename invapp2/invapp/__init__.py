from flask import Flask, render_template
from sqlalchemy import inspect, text
from sqlalchemy.exc import NoSuchTableError, OperationalError

from .extensions import db
from .routes import inventory, reports, orders, work, settings, printers
from config import Config
from . import models  # ensure models are registered with SQLAlchemy

def _ensure_item_type_column(engine):
    """Ensure legacy databases have the ``item.type`` column."""
    inspector = inspect(engine)
    try:
        item_columns = {col["name"] for col in inspector.get_columns("item")}
    except (NoSuchTableError, OperationalError):
        item_columns = set()

    if "type" not in item_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE item ADD COLUMN type VARCHAR"))


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
        _ensure_item_type_column(db.engine)

    # register blueprints
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)

    @app.route("/")
    def home():
        return render_template("home.html")

    return app
