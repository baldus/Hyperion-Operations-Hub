import secrets

from flask import Blueprint, redirect, render_template, request, session, url_for

from invapp.auth import page_access_required
from invapp.login import current_user
from invapp.models import BackupRun
from invapp.superuser import is_superuser

bp = Blueprint("settings", __name__, url_prefix="/settings")

_CLEAR_INVENTORY_CONFIRMATION = "CLEAR INVENTORY"


def _clear_inventory_csrf_token() -> str:
    token = session.get("clear_inventory_csrf")
    if not token:
        token = secrets.token_urlsafe(16)
        session["clear_inventory_csrf"] = token
    return token


@bp.route("/")
@page_access_required("settings")
def settings_home():
    show_clear_inventory = current_user.is_authenticated and (
        current_user.has_role("admin") or is_superuser()
    )
    last_backup = None
    csrf_token = None
    if show_clear_inventory:
        last_backup = BackupRun.query.order_by(BackupRun.started_at.desc()).first()
        csrf_token = _clear_inventory_csrf_token()
    return render_template(
        "settings/home.html",
        last_backup=last_backup,
        clear_inventory_csrf_token=csrf_token,
        clear_inventory_phrase=_CLEAR_INVENTORY_CONFIRMATION,
    )


# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme", methods=["POST", "GET"])
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"

    next_target = request.form.get("next") or request.args.get("next") or request.referrer
    if not next_target:
        next_target = url_for("settings.settings_home")
    return redirect(next_target)
