from flask import Blueprint, redirect, render_template, session, url_for

from invapp.auth import refresh_logged_in_user
from invapp.extensions import login_manager

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.before_request
def require_login():
    if session.get("is_admin"):
        return None
    if refresh_logged_in_user():
        return None
    return login_manager.unauthorized()

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")

# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme")
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"
    return redirect(url_for("settings.settings_home"))
