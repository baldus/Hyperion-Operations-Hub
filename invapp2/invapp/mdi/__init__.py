# invapp2/invapp/__init__.py

def create_app(config_object=None):
    app = Flask(__name__)

    # ... existing config & extension init ...

    # Existing blueprint imports (example)
    # from invapp.routes.inventory import inventory_bp
    # app.register_blueprint(inventory_bp)

    # NEW: MDI blueprint
    from invapp.mdi import mdi_bp
    app.register_blueprint(mdi_bp)

    return app
