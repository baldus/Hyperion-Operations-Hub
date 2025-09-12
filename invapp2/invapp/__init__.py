from flask import Flask, render_template
from .extensions import db
from .routes import inventory, reports, orders, work, settings
from config import Config

def create_app():
    app = Flask(__name__)

    # load configuration from environment variables
    app.config.from_object(Config)

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
