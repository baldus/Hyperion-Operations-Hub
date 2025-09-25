from flask import abort, Blueprint, current_app, render_template, request, session

from invapp.login import login_required
from invapp.models import User

bp = Blueprint("printers", __name__, url_prefix="/settings/printers")

@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@login_required
def printer_settings():
    theme = session.get("theme", "dark")
    session_admin = bool(session.get("is_admin", False))
    user_id = session.get("_user_id")
    user_record = User.query.get(int(user_id)) if user_id is not None else None
    role_admin = bool(user_record and user_record.has_role("admin"))
    is_admin = session_admin or role_admin
    printer_host = current_app.config.get("ZEBRA_PRINTER_HOST", "")
    printer_port = current_app.config.get("ZEBRA_PRINTER_PORT", "")
    message = None

    if request.method == "POST":
        if "theme" in request.form:
            theme = request.form["theme"]
            session["theme"] = theme
        elif not is_admin:
            username = request.form.get("username")
            password = request.form.get("password")
            if username == current_app.config.get("ADMIN_USER", "admin") and password == current_app.config.get("ADMIN_PASSWORD", "password"):
                session["is_admin"] = True
                session_admin = True
                is_admin = True
            else:
                message = "Invalid credentials"
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

    if not is_admin:
        abort(403)

    return render_template(
        "settings/printer_settings.html",
        theme=theme,
        is_admin=is_admin,
        printer_host=printer_host,
        printer_port=printer_port,
        message=message,
    )
