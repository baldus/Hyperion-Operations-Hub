from flask import Blueprint, render_template, request, session, current_app

from invapp.security import admin_required

bp = Blueprint("printers", __name__, url_prefix="/settings/printers")

@bp.route("", methods=["GET", "POST"])
@bp.route("/", methods=["GET", "POST"])
@admin_required
def printer_settings():
    theme = session.get("theme", "dark")
    printer_host = current_app.config.get("ZEBRA_PRINTER_HOST", "")
    printer_port = current_app.config.get("ZEBRA_PRINTER_PORT", "")
    message = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "theme":
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
        printer_host=printer_host,
        printer_port=printer_port,
        message=message,
    )
