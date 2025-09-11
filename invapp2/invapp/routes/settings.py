from flask import Blueprint, render_template, redirect, url_for, session

from .printers import bp as printers_bp

bp = Blueprint("settings", __name__, url_prefix="/settings")

bp.register_blueprint(printers_bp)

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")

# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme")
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"
    return redirect(url_for("settings.settings_home"))
