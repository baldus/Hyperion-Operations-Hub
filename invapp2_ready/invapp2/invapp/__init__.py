from flask import Flask, render_template
from .extensions import db
from .routes import inventory, reports, orders, work, settings

def create_app():
    app = Flask(__name__)

    # database config (example: using PostgreSQL)
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://inv:change_me@localhost/invdb"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = "supersecret"  # needed for flash()

    # ✅ init db with app
    db.init_app(app)

    # register blueprints
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)

    @app.route("/")
    def home():
        return render_template("home.html")

    return app
