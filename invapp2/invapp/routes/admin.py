import io
import json
import time
from datetime import date, datetime, time as time_type
from decimal import Decimal
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


def _redirect_non_admin():
    if session.get("is_admin"):
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
