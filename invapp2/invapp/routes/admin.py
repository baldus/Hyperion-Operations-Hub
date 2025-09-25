import io
import json
import time
from datetime import date, datetime, time as time_type
from decimal import Decimal
from typing import Optional
from urllib.parse import urljoin

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy.sql.sqltypes import Date as SQLDate
from sqlalchemy.sql.sqltypes import DateTime as SQLDateTime
from sqlalchemy.sql.sqltypes import Numeric

from invapp.extensions import db
from invapp.models import Role, User


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


def _has_admin_privileges() -> bool:
    """Return ``True`` if the session has elevated administrator access."""

    if session.get("is_admin"):
        return True

    user_id = session.get("_user_id")
    if user_id is None:
        return False

    try:
        user_id_value = int(user_id)
    except (TypeError, ValueError):
        return False

    user = User.query.get(user_id_value)
    if not user or not user.is_active:
        return False

    return user.has_role("admin")


def _active_admin_count(exclude_user_id: Optional[int] = None) -> int:
    """Return the number of active users who hold the ``admin`` role."""

    query = (
        User.query.join(User.roles)
        .filter(User._is_active.is_(True), Role.name == "admin")
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def _ensure_core_roles() -> None:
    """Ensure the built-in roles exist for the management interface."""

    defaults = {
        "user": "Standard access to operational tools.",
        "admin": "Full administrative control, including user management.",
    }

    updated = False
    for name, description in defaults.items():
        role = Role.query.filter_by(name=name).first()
        if role is None:
            db.session.add(Role(name=name, description=description))
            updated = True
        elif description and role.description != description:
            role.description = description
            updated = True

    if updated:
        db.session.commit()


def _redirect_non_admin():
    if _has_admin_privileges():
        return None

    flash("Admin access required.", "warning")
    next_url = request.full_path if request.query_string else request.path
    return redirect(url_for("admin.login", next=next_url))


def _serialize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, time_type):
        return value.isoformat()
    return value


def _parse_value(column, value):
    if value is None:
        return None

    column_type = column.type
    if isinstance(column_type, SQLDateTime):
        return datetime.fromisoformat(value)
    if isinstance(column_type, SQLDate):
        return date.fromisoformat(value)
    if isinstance(column_type, Numeric):
        return Decimal(value)
    return value


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


@bp.route("/data-backup")
def data_backup():
    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    table_names = [table.name for table in db.Model.metadata.sorted_tables]
    return render_template("admin/data_backup.html", table_names=table_names)


@bp.route("/data-backup/export", methods=["POST"])
def export_data():
    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    data = {}
    for table in db.Model.metadata.sorted_tables:
        result = db.session.execute(table.select()).mappings()
        data[table.name] = [
            {key: _serialize_value(value) for key, value in row.items()}
            for row in result
        ]

    payload = json.dumps(data, indent=2).encode("utf-8")
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"hyperion-backup-{timestamp}.json"
    return send_file(
        io.BytesIO(payload),
        mimetype="application/json",
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/data-backup/import", methods=["POST"])
def import_data():
    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        flash("Please choose a backup file to upload.", "warning")
        return redirect(url_for("admin.data_backup"))

    try:
        payload = upload.read()
        raw_data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        flash("The uploaded file is not a valid backup.", "danger")
        return redirect(url_for("admin.data_backup"))

    metadata = db.Model.metadata

    try:
        for table in reversed(metadata.sorted_tables):
            db.session.execute(table.delete())

        for table in metadata.sorted_tables:
            table_name = table.name
            rows = raw_data.get(table_name, [])
            prepared_rows = []
            for row in rows:
                prepared = {}
                for column in table.columns:
                    value = row.get(column.name)
                    prepared[column.name] = _parse_value(column, value)
                prepared_rows.append(prepared)
            if prepared_rows:
                db.session.execute(table.insert(), prepared_rows)

        db.session.commit()
    except Exception as exc:  # pragma: no cover - defensive rollback
        db.session.rollback()
        current_app.logger.exception("Failed to import backup: %s", exc)
        flash("Import failed. No changes were applied.", "danger")
        return redirect(url_for("admin.data_backup"))

    flash("Backup imported successfully.", "success")
    return redirect(url_for("admin.data_backup"))


@bp.route("/users", methods=["GET"])
def manage_users():
    """Render the management console for application accounts."""

    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    _ensure_core_roles()
    users = User.query.order_by(User.username).all()
    roles = Role.query.order_by(Role.name).all()
    return render_template("admin/user_management.html", users=users, roles=roles)


@bp.route("/users", methods=["POST"])
def create_user():
    """Create a new application user from the management console."""

    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    _ensure_core_roles()

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_active = bool(request.form.get("is_active"))
    selected_roles = request.form.getlist("roles")

    if not username or not password:
        flash("Username and password are required to create an account.", "danger")
        return redirect(url_for("admin.manage_users"))

    if User.query.filter_by(username=username).first():
        flash("That username is already in use.", "danger")
        return redirect(url_for("admin.manage_users"))

    user = User(username=username)
    user.set_password(password)
    user.is_active = is_active

    roles_to_assign = []
    if selected_roles:
        roles_to_assign = Role.query.filter(Role.name.in_(selected_roles)).all()
    if not roles_to_assign:
        default_role = Role.query.filter_by(name="user").first()
        if default_role:
            roles_to_assign = [default_role]

    for role in roles_to_assign:
        user.roles.append(role)

    db.session.add(user)
    db.session.commit()

    flash("User account created successfully.", "success")
    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/update", methods=["POST"])
def update_user(user_id: int):
    """Update role assignments or activation status for a user."""

    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    _ensure_core_roles()

    user = User.query.get_or_404(user_id)
    selected_roles = request.form.getlist("roles")
    is_active = bool(request.form.get("is_active"))

    roles_to_assign = []
    if selected_roles:
        roles_to_assign = Role.query.filter(Role.name.in_(selected_roles)).all()
    if not roles_to_assign:
        default_role = Role.query.filter_by(name="user").first()
        if default_role:
            roles_to_assign = [default_role]

    removing_admin = user.has_role("admin") and all(
        role.name != "admin" for role in roles_to_assign
    )
    deactivating_admin = user.has_role("admin") and not is_active

    if (removing_admin or deactivating_admin) and _active_admin_count(
        exclude_user_id=user.id
    ) == 0:
        flash("At least one active administrator is required.", "warning")
        return redirect(url_for("admin.manage_users"))

    user.roles = roles_to_assign
    user.is_active = is_active
    db.session.commit()

    flash("User access updated.", "success")
    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/password", methods=["POST"])
def reset_user_password(user_id: int):
    """Reset the password for a user account."""

    redirect_response = _redirect_non_admin()
    if redirect_response:
        return redirect_response

    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if not new_password:
        flash("A new password is required.", "danger")
        return redirect(url_for("admin.manage_users"))

    if new_password != confirm_password:
        flash("Passwords do not match.", "danger")
        return redirect(url_for("admin.manage_users"))

    user = User.query.get_or_404(user_id)
    user.set_password(new_password)
    db.session.commit()

    flash("Password updated for user.", "success")
    return redirect(url_for("admin.manage_users"))
