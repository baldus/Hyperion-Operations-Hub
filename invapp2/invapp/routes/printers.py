from flask import Blueprint, current_app, render_template, request, session
from flask_login import current_user

from invapp.extensions import login_manager
from invapp.models import User

bp = Blueprint(
    "printers",
    __name__,
    url_prefix="/settings/printers",
)


@bp.before_request
def require_login_or_admin_session():
    if session.get("is_admin"):
        return None
    if current_user.is_authenticated:
        return None
    return login_manager.unauthorized()


def _has_admin_access() -> bool:
    if session.get("is_admin"):
        return True
    if not current_user.is_authenticated:
        return False
    user_id = session.get("_user_id")
    if not user_id:
        return False
    user = User.query.get(int(user_id))
    if not user:
        return False
    return user.has_role("admin")


@bp.route("/", methods=["GET", "POST"])
@bp.route("", methods=["GET", "POST"])
def printer_settings():
    theme = session.get("theme", "dark")
    printer_host = current_app.config.get("ZEBRA_PRINTER_HOST", "")
    printer_port = current_app.config.get("ZEBRA_PRINTER_PORT", "")
    message = None

    is_admin = _has_admin_access()

    if request.method == "POST":
        if "theme" in request.form:
            theme = request.form["theme"]
            session["theme"] = theme
        elif not is_admin:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if (
                username == current_app.config.get("ADMIN_USER", "admin")
                and password
                == current_app.config.get("ADMIN_PASSWORD", "password")
            ):
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

    status_code = 200 if is_admin else 403

    return (
        render_template(
            "settings/printer_settings.html",
            theme=theme,
            is_admin=is_admin,
            printer_host=printer_host,
            printer_port=printer_port,
            message=message,
        ),
        status_code,
    )
