from flask import Flask, render_template
from sqlalchemy import inspect, text
from sqlalchemy.exc import NoSuchTableError

from .extensions import db
from .routes import inventory, reports, orders, work, settings, printers
from config import Config
from . import models  # ensure models are registered with SQLAlchemy

def create_app():
    app = Flask(__name__)

    # load configuration from environment variables
    app.config.from_object(Config)

    # ✅ init db with app
    db.init_app(app)
    # create tables if they do not exist and ensure legacy schema has "type"
    with app.app_context():
        db.create_all()

        inspector = inspect(db.engine)
        try:
            item_columns = {col["name"] for col in inspector.get_columns("item")}
        except NoSuchTableError:
            item_columns = set()

        if "type" not in item_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE item ADD COLUMN type VARCHAR"))

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
