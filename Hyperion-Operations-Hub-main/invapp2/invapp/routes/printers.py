"""Administrative printer configuration routes."""

from flask import abort, Blueprint, current_app, render_template, request, session
from flask_login import current_user

from invapp.auth import refresh_logged_in_user
from invapp.extensions import login_manager


bp = Blueprint("printers", __name__, url_prefix="/settings/printers")


@bp.before_request
def ensure_authenticated():
    """Require either a logged-in user or an active admin session."""

    if session.get("is_admin"):
        return None
    user = refresh_logged_in_user()
    if user:
        return None
    return login_manager.unauthorized()


@bp.route("/", methods=["GET", "POST"])
def printer_settings():
    theme = session.get("theme", "dark")
    is_admin_session = session.get("is_admin", False)
    has_admin_role = current_user.is_authenticated and current_user.has_role("admin")
    can_manage = is_admin_session or has_admin_role

    if not can_manage:
        abort(403)

    printer_host = current_app.config.get("ZEBRA_PRINTER_HOST", "")
    printer_port = current_app.config.get("ZEBRA_PRINTER_PORT", "")
    message = None

    if request.method == "POST":
        if "theme" in request.form:
            theme = request.form["theme"]
            session["theme"] = theme
        else:
            printer_host = request.form.get("printer_host", printer_host)
            printer_port = request.form.get("printer_port", printer_port)
            current_app.config["ZEBRA_PRINTER_HOST"] = printer_host
            if printer_port:
                try:
                    current_app.config["ZEBRA_PRINTER_PORT"] = int(printer_port)
                except ValueError:
                    message = "Port must be a number"
            message = message or "Settings updated"

    return render_template(
        "settings/printer_settings.html",
        theme=theme,
        is_admin=can_manage,
        printer_host=printer_host,
        printer_port=printer_port,
        message=message,
    )


bp.add_url_rule("", view_func=printer_settings, methods=["GET", "POST"])
