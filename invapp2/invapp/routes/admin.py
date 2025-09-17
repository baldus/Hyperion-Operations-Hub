import time
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
            session["admin_last_active"] = time.time()
            flash("Admin access granted.", "success")
            return redirect(_get_safe_redirect_target())

        message = "Invalid credentials"

    return render_template("admin/login.html", is_admin=session.get("is_admin", False), message=message)


@bp.route("/logout")
def logout():
    """Clear the admin session state."""

    was_admin = session.pop("is_admin", None)
    session.pop("admin_last_active", None)
    if was_admin:
        flash("Admin access revoked.", "info")
    return redirect(_get_safe_redirect_target())


@bp.before_app_request
def enforce_admin_session_timeout():
    """Automatically revoke admin access after inactivity."""

    if not session.get("is_admin"):
        session.pop("admin_last_active", None)
        return

    timeout = current_app.config.get("ADMIN_SESSION_TIMEOUT", 300)
    try:
        timeout_seconds = int(timeout)
    except (TypeError, ValueError):
        timeout_seconds = 300

    now = time.time()
    last_active = session.get("admin_last_active")
    if last_active is not None:
        try:
            last_active_value = float(last_active)
        except (TypeError, ValueError):
            last_active_value = None
    else:
        last_active_value = None

    if last_active_value is not None and now - last_active_value > timeout_seconds:
        session.pop("is_admin", None)
        session.pop("admin_last_active", None)
        flash("Admin session has timed out due to inactivity.", "info")

        login_endpoint = request.endpoint == "admin.login"
        if not login_endpoint:
            next_url = request.full_path if request.query_string else request.path
            return redirect(url_for("admin.login", next=next_url))
        return

    session["admin_last_active"] = now
