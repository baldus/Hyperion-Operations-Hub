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
    session,
    url_for,
)
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.sqltypes import Date as SQLDate
from sqlalchemy.sql.sqltypes import DateTime as SQLDateTime
from sqlalchemy.sql.sqltypes import Numeric

from invapp import models
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
            "label": "Access Log",
            "href": url_for("admin.access_log"),
        },
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
            "href": url_for("admin.storage_locations"),
        },
    ]

    return render_template(
        "admin/tools.html",
        system_health=system_health,
        quick_links=quick_links,
    )


@bp.route("/access-log")
@login_required
@require_roles("admin")
def access_log():
    filters = {
        "ip": (request.args.get("ip") or "").strip(),
        "username": (request.args.get("username") or "").strip(),
        "event_type": (request.args.get("event_type") or "").strip(),
    }

    query = models.AccessLog.query
    if filters["ip"]:
        query = query.filter(models.AccessLog.ip_address == filters["ip"])
    if filters["username"]:
        query = query.filter(models.AccessLog.username == filters["username"])
    if filters["event_type"]:
        query = query.filter(models.AccessLog.event_type == filters["event_type"])

    entries = (
        query.order_by(models.AccessLog.occurred_at.desc())
        .limit(500)
        .all()
    )

    ip_summary = (
        db.session.query(
            models.AccessLog.ip_address,
            func.count(models.AccessLog.id).label("total"),
        )
        .group_by(models.AccessLog.ip_address)
        .order_by(func.count(models.AccessLog.id).desc())
        .limit(20)
        .all()
    )

    event_summary = (
        db.session.query(
            models.AccessLog.event_type,
            func.count(models.AccessLog.id).label("total"),
        )
        .group_by(models.AccessLog.event_type)
        .all()
    )

    event_options = [
        (key, label)
        for key, label in models.AccessLog.EVENT_LABELS.items()
    ]

    return render_template(
        "admin/access_log.html",
        entries=entries,
        filters=filters,
        ip_summary=ip_summary,
        event_summary=event_summary,
        event_options=event_options,
        models=models,
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


@bp.route("/storage-locations", methods=["GET", "POST"])
@login_required
@require_roles("admin")
def storage_locations():
    current_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    display_current_url = _display_database_url(current_url)
    engine = db.get_engine()
    engine_name = getattr(engine, "name", str(engine))

    migration_summary = session.pop("storage_migration_summary", None)

    if request.method == "POST":
        action = request.form.get("action") or ""
        target_url = (request.form.get("new_database_url") or "").strip()

        if not target_url:
            flash("Please provide a database connection URL.", "warning")
            return redirect(url_for("admin.storage_locations"))

        if target_url == current_url:
            flash("The new database location must be different from the current one.", "warning")
            return redirect(url_for("admin.storage_locations"))

        if action == "test":
            if _test_database_connection(target_url):
                flash("Successfully connected to the target database.", "success")
            else:
                flash("Could not connect to the target database. Check the URL and credentials.", "danger")
            return redirect(url_for("admin.storage_locations"))

        if action == "migrate":
            confirmation = (request.form.get("confirm_phrase") or "").strip().lower()
            if confirmation != "migrate":
                flash("Type 'migrate' in the confirmation box to start the migration.", "warning")
                return redirect(url_for("admin.storage_locations"))

            try:
                summary = _migrate_database(target_url)
            except ValueError as exc:
                flash(str(exc), "warning")
            except SQLAlchemyError as exc:
                current_app.logger.exception("Database migration failed")
                flash(f"Migration failed: {exc}", "danger")
            except Exception as exc:  # pragma: no cover - defensive guard
                current_app.logger.exception("Unexpected error during migration")
                flash("An unexpected error occurred during migration.", "danger")
            else:
                session["storage_migration_summary"] = summary
                flash(
                    "Database copied to the new location. Update the DB_URL environment variable to begin using it.",
                    "success",
                )
            return redirect(url_for("admin.storage_locations"))

        flash("Unsupported action requested.", "warning")
        return redirect(url_for("admin.storage_locations"))

    storage_directories = _gather_storage_directories()

    return render_template(
        "admin/storage_locations.html",
        current_database_url=display_current_url,
        engine_name=engine_name,
        storage_directories=storage_directories,
        migration_summary=migration_summary,
    )


def _display_database_url(raw_url: str) -> str:
    if not raw_url:
        return "Unknown"
    try:
        url = make_url(raw_url)
    except Exception:
        return raw_url

    if url.password:
        url = url.set(password="••••••")
    return str(url)


def _test_database_connection(target_url: str) -> bool:
    try:
        engine = create_engine(target_url)
    except Exception:
        return False

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    finally:
        engine.dispose()
    return True


def _migrate_database(target_url: str) -> list[dict[str, int | str]]:
    if not target_url:
        raise ValueError("A target database URL is required.")

    target_engine = create_engine(target_url)
    metadata = db.Model.metadata

    db.session.flush()

    try:
        metadata.create_all(target_engine)
        summary: list[dict[str, int | str]] = []
        with target_engine.begin() as target_conn:
            for table in reversed(metadata.sorted_tables):
                target_conn.execute(table.delete())

            for table in metadata.sorted_tables:
                rows = [dict(row) for row in db.session.execute(table.select()).mappings()]
                if rows:
                    target_conn.execute(table.insert(), rows)
                summary.append({"table": table.name, "rows": len(rows)})

        try:
            from invapp import (
                _ensure_inventory_schema,
                _ensure_order_schema,
                _ensure_production_schema,
            )

            _ensure_inventory_schema(target_engine)
            _ensure_order_schema(target_engine)
            _ensure_production_schema(target_engine)
        except Exception:
            current_app.logger.exception("Failed to ensure schema on target database")
    finally:
        target_engine.dispose()

    return summary


def _gather_storage_directories() -> list[dict[str, object]]:
    paths = [
        (
            "Work Instructions",
            current_app.config.get("WORK_INSTRUCTION_UPLOAD_FOLDER"),
        ),
        (
            "Item Attachments",
            current_app.config.get("ITEM_ATTACHMENT_UPLOAD_FOLDER"),
        ),
        (
            "Quality Attachments",
            current_app.config.get("QUALITY_ATTACHMENT_UPLOAD_FOLDER"),
        ),
    ]

    directories: list[dict[str, object]] = []
    for label, path in paths:
        if not path:
            directories.append(
                {
                    "label": label,
                    "path": "Not configured",
                    "exists": False,
                    "file_count": 0,
                    "size_bytes": 0,
                    "size_display": "0 B",
                }
            )
            continue

        path = os.path.abspath(path)
        exists = os.path.isdir(path)
        size_bytes = 0
        file_count = 0
        if exists:
            for root, _, files in os.walk(path):
                for filename in files:
                    file_count += 1
                    file_path = os.path.join(root, filename)
                    try:
                        size_bytes += os.path.getsize(file_path)
                    except OSError:
                        continue

        directories.append(
            {
                "label": label,
                "path": path,
                "exists": exists,
                "file_count": file_count,
                "size_bytes": size_bytes,
                "size_display": _format_bytes(float(size_bytes)) if size_bytes else "0 B",
            }
        )

    return directories
