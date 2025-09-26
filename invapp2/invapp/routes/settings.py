from flask import Blueprint, render_template, redirect, url_for, session

from invapp.auth import blueprint_page_guard

bp = Blueprint("settings", __name__, url_prefix="/settings")

bp.before_request(blueprint_page_guard("settings"))

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")


@bp.route("/operations")
def operations_menu():
    return render_template("settings/operations_menu.html")

# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme")
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"
    return redirect(url_for("settings.settings_home"))
