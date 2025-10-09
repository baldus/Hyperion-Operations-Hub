from urllib.parse import urljoin, urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from invapp.audit import record_login_event
from invapp.extensions import db
from invapp.login import current_user, login_required, login_user, logout_user
from invapp.models import AccessLog, User

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/register", methods=["GET", "POST"])
@login_required
def register():
    admin_username = current_app.config.get("ADMIN_USER", "superuser")
    if current_user.username != admin_username:
        abort(404)

    flash("User management has moved to the dedicated admin tools.", "info")
    return redirect(url_for("users.create"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            record_login_event(
                event_type=AccessLog.EVENT_LOGIN_SUCCESS,
                user_id=user.id,
                username=user.username,
                status_code=302,
            )
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            flash("Logged in", "success")
            return redirect(url_for("home"))
        record_login_event(
            event_type=AccessLog.EVENT_LOGIN_FAILURE,
            user_id=user.id if user else None,
            username=username,
            status_code=401,
        )
        flash("Invalid credentials", "danger")
    return render_template("auth/login.html")


def _safe_redirect_target(target: str | None, *, default: str) -> str:
    if not target:
        return url_for(default)

    # Allow internal relative URLs directly ("/foo" or "foo")
    if target.startswith("/"):
        return target

    app_url = urlparse(request.host_url)
    parsed_target = urlparse(urljoin(request.host_url, target))
    if parsed_target.netloc == app_url.netloc:
        return parsed_target.path + (f"?{parsed_target.query}" if parsed_target.query else "")

    return url_for(default)


@bp.route("/logout")
@login_required
def logout():
    user_id = current_user.id if current_user.is_authenticated else None
    username = current_user.username if current_user.is_authenticated else None
    logout_user()
    record_login_event(
        event_type=AccessLog.EVENT_LOGOUT,
        user_id=user_id,
        username=username,
        status_code=302,
    )
    flash("Logged out", "success")

    fallback = url_for("home")
    next_target = request.args.get("next")
    if not next_target:
        next_target = request.referrer

    redirect_target = _safe_redirect_target(next_target, default="home")
    if redirect_target == url_for("home") and fallback:
        return redirect(fallback)

    return redirect(redirect_target)


@bp.route("/reset-password", methods=["GET", "POST"])
@login_required
def reset_password():
    if request.method == "POST":
        old = request.form["old_password"].strip()
        new = request.form["new_password"].strip()
        if current_user.check_password(old):
            current_user.set_password(new)
            db.session.commit()
            flash("Password updated", "success")
            return redirect(url_for("home"))
        flash("Invalid current password", "danger")
    return render_template("auth/reset_password.html")
