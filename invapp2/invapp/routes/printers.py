from flask import Blueprint, render_template, request, session, current_app

bp = Blueprint("printers", __name__, url_prefix="/settings/printers")

@bp.route("/", methods=["GET", "POST"])
def printer_settings():
    theme = session.get("theme", "dark")
    is_admin = session.get("is_admin", False)
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

    return render_template(
        "settings/printer_settings.html",
        theme=theme,
        is_admin=is_admin,
        printer_host=printer_host,
        printer_port=printer_port,
        message=message,
    )
