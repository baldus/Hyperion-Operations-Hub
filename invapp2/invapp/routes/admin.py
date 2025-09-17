from urllib.parse import urljoin

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_safe_redirect_target(default: str = "home") -> str:
    """Return a safe redirect target within the application."""

    next_url = request.args.get("next")
    if not next_url:
        return url_for(default)

    # Ensure the redirect target stays within the current host.
    host_url = request.host_url
    absolute_target = urljoin(host_url, next_url)
    if absolute_target.startswith(host_url):
        login_url = url_for("admin.login")
        next_path = next_url.split("?")[0]
        if not next_path.startswith(login_url):
            return next_url

    return url_for(default)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Allow privileged administrators to unlock admin-only features."""

    is_admin = session.get("is_admin", False)
    message = None

    if request.method == "POST" and not is_admin:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin_user = current_app.config.get("ADMIN_USER", "admin")
        admin_password = current_app.config.get("ADMIN_PASSWORD", "password")

        if username == admin_user and password == admin_password:
            session["is_admin"] = True
            flash("Admin access granted.", "success")
            return redirect(_get_safe_redirect_target())

        message = "Invalid credentials"

    return render_template("admin/login.html", is_admin=session.get("is_admin", False), message=message)
