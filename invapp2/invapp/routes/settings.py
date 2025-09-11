from flask import Blueprint, render_template, redirect, url_for, session
from flask_login import login_required

from invapp.auth import role_required

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.before_request
@login_required
def require_login():
    pass

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")


@bp.route("/printers")
@role_required("admin")
def printers_home():
    return render_template("settings/printers.html")

# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme")
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"
    return redirect(url_for("settings.settings_home"))
