"""Application factory for the inventory app.

This file now wires in the ``LoginManager`` from :mod:`flask_login` to
support user sessions and authentication across the application.
"""

from flask import Flask, render_template
from .extensions import db, login_manager
from .routes import inventory, reports, orders, work, settings, auth
from .models import User


def create_app(config=None):
    app = Flask(__name__)

    # database config (example: using PostgreSQL)
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://inv:change_me@localhost/invdb"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = "supersecret"  # needed for flash() / sessions
    app.config.setdefault("SESSION_PERMANENT", False)
    if config:
        app.config.update(config)

    # ✅ init db with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    # register blueprints
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(auth.bp)

    @app.route("/")
    def home():
        return render_template("home.html")

    return app
