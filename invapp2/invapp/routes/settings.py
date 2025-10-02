from flask import Blueprint, redirect, render_template, request, session, url_for

from invapp.auth import page_access_required

bp = Blueprint("settings", __name__, url_prefix="/settings")

@bp.route("/")
@page_access_required("settings")
def settings_home():
    return render_template("settings/home.html")


# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme", methods=["POST", "GET"])
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"

    next_target = request.form.get("next") or request.args.get("next") or request.referrer
    if not next_target:
        next_target = url_for("settings.settings_home")
    return redirect(next_target)
