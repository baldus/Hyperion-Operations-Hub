import io
import json
import os
import shutil
from datetime import date, datetime, time as time_type, timedelta
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
    url_for,
)
from sqlalchemy.sql.sqltypes import Date as SQLDate
from sqlalchemy.sql.sqltypes import DateTime as SQLDateTime
from sqlalchemy.sql.sqltypes import Numeric

from invapp.extensions import db
from invapp.login import current_user, login_required, logout_user
from invapp.security import require_roles


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


@bp.route("/login")
def login():
    """Redirect users to proper authentication and surface admin shortcuts."""

    if not current_user.is_authenticated:
        next_target = request.args.get("next")
        login_url = url_for("auth.login")
        if next_target:
            login_url = f"{login_url}?next={next_target}"
        return redirect(login_url)

    if not current_user.has_role("admin"):
        flash("Administrator privileges are required to manage these tools.", "warning")
        return redirect(_get_safe_redirect_target())

    return render_template("admin/login.html")


@bp.route("/logout")
@login_required
def logout():
    """Sign out the authenticated user."""

    logout_user()
    flash("You have been signed out.", "info")
    return redirect(_get_safe_redirect_target())


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "Unavailable"

    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _read_uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            value = fh.read().strip().split()[0]
            return float(value)
    except (OSError, ValueError, IndexError):
        return None


def _memory_snapshot() -> dict[str, float] | None:
    fields: dict[str, float] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                parts = raw_value.strip().split()
                if not parts:
                    continue
                try:
                    fields[key] = float(parts[0]) * 1024
                except ValueError:
                    continue
    except OSError:
        return None

    total = fields.get("MemTotal")
    available = fields.get("MemAvailable") or fields.get("MemFree")
    if not total:
        return None
    used = total - (available or 0)
    percent = (used / total) * 100 if total else 0.0
    return {
        "total": total,
        "available": available or 0.0,
        "used": used,
        "percent": percent,
    }


def _disk_snapshot(path: str = "/") -> dict[str, float] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None

    percent = (usage.used / usage.total) * 100 if usage.total else 0.0
    return {
        "total": float(usage.total),
        "used": float(usage.used),
        "free": float(usage.free),
        "percent": percent,
    }


def _format_bytes(value: float) -> str:
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if value < step or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} PB"


def _status_level(value: float, *, warn: float, alert: float) -> str:
    if value >= alert:
        return "alert"
    if value >= warn:
        return "warn"
    return "ok"


@bp.route("/tools")
@login_required
@require_roles("admin")
def tools():
    uptime_seconds = _read_uptime_seconds()
    boot_time_display = None
    if uptime_seconds is not None:
        boot_time = datetime.utcnow() - timedelta(seconds=int(uptime_seconds))
        boot_time_display = boot_time.strftime("%b %d %H:%M UTC")

    cpu_count = os.cpu_count() or 1
    try:
        load_averages = os.getloadavg()
    except OSError:
        load_averages = None

    memory = _memory_snapshot()
    disk = _disk_snapshot()

    system_health: list[dict[str, object]] = [
        {
            "title": "System Uptime",
            "metric": _format_duration(uptime_seconds),
            "meta": f"Since {boot_time_display}" if boot_time_display else "Start time unavailable",
            "level": "ok",
        }
    ]

    if load_averages:
        load_ratio = load_averages[0] / max(cpu_count, 1)
        system_health.append(
            {
                "title": "CPU Load (1 min)",
                "metric": f"{load_averages[0]:.2f}",
                "meta": f"5 min {load_averages[1]:.2f} • 15 min {load_averages[2]:.2f} on {cpu_count} cores",
                "level": _status_level(load_ratio, warn=0.75, alert=1.0),
            }
        )

    if disk:
        system_health.append(
            {
                "title": "Disk Usage",
                "metric": f"{disk['percent']:.1f}%",
                "meta": f"{_format_bytes(disk['free'])} free of {_format_bytes(disk['total'])}",
                "level": _status_level(disk["percent"], warn=70, alert=90),
            }
        )

    memory_card = None
    if memory:
        percent = max(0.0, min(memory["percent"], 100.0))
        memory_card = {
            "title": "Memory Usage",
            "metric": f"{percent:.1f}%",
            "meta": f"{_format_bytes(memory['used'])} used of {_format_bytes(memory['total'])}",
            "level": _status_level(percent, warn=70, alert=85),
            "progress": {
                "percent": percent,
                "left": f"{_format_bytes(memory['used'])} used",
                "right": f"{_format_bytes(memory['available'])} free",
            },
        }
        system_health.append(memory_card)

    quick_links = [
        {
            "label": "Reports Dashboard",
            "href": url_for("reports.reports_home"),
        },
        {
            "label": "Data Backup",
            "href": url_for("admin.data_backup"),
        },
        {
            "label": "Data Storage Locations",
            "href": "#",
            "disabled": True,
        },
    ]

    return render_template(
        "admin/tools.html",
        system_health=system_health,
        quick_links=quick_links,
    )


@bp.route("/data-backup")
@login_required
@require_roles("admin")
def data_backup():
    table_names = [table.name for table in db.Model.metadata.sorted_tables]
    return render_template("admin/data_backup.html", table_names=table_names)


@bp.route("/data-backup/export", methods=["POST"])
@login_required
@require_roles("admin")
def export_data():
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
@login_required
@require_roles("admin")
def import_data():
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
