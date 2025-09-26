from flask import Blueprint, current_app, render_template, request, session

from invapp.auth import blueprint_page_guard
from invapp.login import current_user, login_required
from invapp.security import require_roles

bp = Blueprint("printers", __name__, url_prefix="/settings/printers")

bp.before_request(blueprint_page_guard("printers"))

@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@login_required
@require_roles("admin")
def printer_settings():
    theme = session.get("theme", "dark")
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
        is_admin=current_user.has_role("admin"),
        printer_host=printer_host,
        printer_port=printer_port,
        message=message,
    )
