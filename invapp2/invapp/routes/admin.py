import io
import json
import os
import shlex
import shutil
import subprocess
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
from invapp.offline import is_emergency_mode_active
from invapp.security import require_roles


bp = Blueprint("admin", __name__, url_prefix="/admin")


_EMERGENCY_COMMAND_GROUPS = (
    {
        "id": "postgresql",
        "title": "PostgreSQL service control",
        "description": "Check the database service and bring it online without leaving the console.",
        "commands": (
            {
                "id": "pg-status",
                "label": "Check service status",
                "command": ("sudo", "systemctl", "status", "postgresql"),
                "note": "Inspect whether PostgreSQL is running and review recent log output.",
            },
            {
                "id": "pg-start",
                "label": "Start PostgreSQL",
                "command": ("sudo", "systemctl", "start", "postgresql"),
                "note": "Launch the database service if it is currently stopped.",
            },
            {
                "id": "pg-restart",
                "label": "Restart PostgreSQL",
                "command": ("sudo", "systemctl", "restart", "postgresql"),
                "note": "Restart the service after configuration or package updates.",
            },
        ),
    },
    {
        "id": "database",
        "title": "Database bootstrap helpers",
        "description": "Create or reset the application database after provisioning a new PostgreSQL instance.",
        "commands": (
            {
                "id": "db-create",
                "label": "Create application database",
                "command": ("sudo", "-u", "postgres", "createdb", "invdb"),
                "note": "Provision the expected \"invdb\" database if it does not already exist.",
            },
            {
                "id": "db-owner",
                "label": "Ensure database owner",
                "command": (
                    "sudo",
                    "-u",
                    "postgres",
                    "psql",
                    "-c",
                    "ALTER DATABASE invdb OWNER TO inv;",
                ),
                "note": "Grant ownership of the application database to the \"inv\" role.",
            },
            {
                "id": "db-user",
                "label": "Create application user",
                "command": (
                    "sudo",
                    "-u",
                    "postgres",
                    "psql",
                    "-c",
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inv') THEN CREATE USER inv WITH PASSWORD 'change_me'; END IF; END $$;",
                ),
                "note": "Create the default \"inv\" role with the documented password if it is missing.",
            },
            {
                "id": "db-grant",
                "label": "Grant privileges",
                "command": (
                    "sudo",
                    "-u",
                    "postgres",
                    "psql",
                    "-c",
                    "GRANT ALL PRIVILEGES ON DATABASE invdb TO inv;",
                ),
                "note": "Ensure the application role can connect once the database is online.",
            },
        ),
    },
    {
        "id": "packages",
        "title": "System package helpers",
        "description": "Download or upgrade prerequisites that PostgreSQL depends on.",
        "commands": (
            {
                "id": "apt-update",
                "label": "Update apt package lists",
                "command": ("sudo", "apt-get", "update"),
                "note": "Refresh repositories before installing or upgrading packages.",
            },
            {
                "id": "apt-install-postgres",
                "label": "Install PostgreSQL server",
                "command": ("sudo", "apt-get", "install", "-y", "postgresql", "postgresql-contrib"),
                "note": "Install the database server and common extensions.",
            },
            {
                "id": "pip-upgrade",
                "label": "Upgrade Python dependencies",
                "command": ("pip", "install", "--upgrade", "-r", "requirements.txt"),
                "note": "Reinstall console Python packages inside the active virtual environment.",
            },
        ),
    },
    {
        "id": "service",
        "title": "Console utilities",
        "description": "Relaunch the operations console after completing recovery tasks.",
        "commands": (
            {
                "id": "console-restart",
                "label": "Restart operations console",
                "command": ("bash", "start_operations_console.sh"),
                "note": "Apply changes and restart the Gunicorn service using the helper script.",
            },
        ),
    },
)


_ALLOWED_CUSTOM_BINARIES = {
    "systemctl",
    "service",
    "psql",
    "createdb",
    "dropdb",
    "pip",
    "pip3",
    "python",
    "python3",
    "bash",
    "sh",
    "curl",
    "wget",
    "apt",
    "apt-get",
    "docker",
}


def _all_emergency_commands() -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for group in _EMERGENCY_COMMAND_GROUPS:
        for command in group["commands"]:
            lookup[command["id"]] = command
    return lookup


def _quote_command(parts: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _validate_custom_command(raw_command: str) -> tuple[str, ...]:
    parts = tuple(shlex.split(raw_command))
    if not parts:
        raise ValueError("Enter a command to run.")

    binary = parts[0]
    if binary == "sudo":
        if len(parts) < 2:
            raise ValueError("Provide the command to run after sudo.")
        if parts[1] not in _ALLOWED_CUSTOM_BINARIES:
            raise ValueError("That command is not allowed in emergency mode.")
        return parts

    if binary not in _ALLOWED_CUSTOM_BINARIES:
        raise ValueError("That command is not allowed in emergency mode.")

    if binary in {"bash", "sh"} and len(parts) > 1:
        script_name = parts[1]
        if script_name not in {"start_operations_console.sh", "start_inventory.sh"}:
            raise ValueError("Only approved helper scripts may be launched from the console.")

    return parts


def _run_emergency_command(parts: tuple[str, ...]) -> dict[str, object]:
    completed = subprocess.run(
        parts,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return {
        "command": _quote_command(parts),
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def _database_available() -> bool:
    return not is_emergency_mode_active()


def _render_offline_page(title: str, *, description: str | None = None):
    return render_template(
        "admin/offline.html",
        title=title,
        description=description,
        recovery_steps=current_app.config.get("DATABASE_RECOVERY_STEPS", ()),
    )


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
    database_online = _database_available()

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
            "label": "Emergency command console",
            "href": url_for("admin.emergency_console"),
            "disabled": False,
            "note": "Run curated recovery commands directly from the browser.",
        },
        {
            "label": "Access Log",
            "href": url_for("admin.access_log") if database_online else None,
            "disabled": not database_online,
            "note": "Requires the database to be online.",
        },
        {
            "label": "Reports Dashboard",
            "href": url_for("reports.reports_home"),
            "disabled": False,
            "note": None,
        },
        {
            "label": "Data Backup",
            "href": url_for("admin.data_backup") if database_online else None,
            "disabled": not database_online,
            "note": "Unavailable while PostgreSQL is offline.",
        },
        {
            "label": "Data Storage Locations",
            "href": url_for("admin.storage_locations") if database_online else None,
            "disabled": not database_online,
            "note": "Requires database access to list folders.",
        },
    ]

    return render_template(
        "admin/tools.html",
        system_health=system_health,
        quick_links=quick_links,
        database_online=database_online,
    )


@bp.route("/emergency-console", methods=["GET", "POST"])
@login_required
@require_roles("admin")
def emergency_console():
    command_lookup = _all_emergency_commands()
    command_groups = _EMERGENCY_COMMAND_GROUPS
    command_result: dict[str, object] | None = None
    error_message: str | None = None
    selected_command_id: str | None = None
    custom_command = (request.form.get("custom_command") or "").strip()

    if request.method == "POST":
        command_id = (request.form.get("command_id") or "").strip()
        try:
            if command_id:
                command = command_lookup.get(command_id)
                if not command:
                    raise ValueError("Unknown command requested.")
                selected_command_id = command_id
                command_result = _run_emergency_command(command["command"])
                command_result["label"] = command["label"]
                command_result["note"] = command.get("note")
            elif custom_command:
                parts = _validate_custom_command(custom_command)
                command_result = _run_emergency_command(parts)
                command_result["label"] = "Custom command"
            else:
                raise ValueError("Select a command or enter a custom command to run.")
        except ValueError as exc:
            error_message = str(exc)
        except subprocess.TimeoutExpired:
            error_message = "The command timed out. Try running it from the terminal for more control."
        except FileNotFoundError as exc:
            error_message = f"Command not found: {exc.filename or exc}"
        except OSError as exc:
            error_message = f"Unable to launch command: {exc}"

    return render_template(
        "admin/emergency_console.html",
        command_groups=command_groups,
        command_result=command_result,
        error_message=error_message,
        selected_command_id=selected_command_id,
        custom_command=custom_command,
        allowed_custom_binaries=sorted(_ALLOWED_CUSTOM_BINARIES),
        database_online=_database_available(),
    )


@bp.route("/access-log")
@login_required
@require_roles("admin")
def access_log():
    if not _database_available():
        return _render_offline_page(
            "Access Log",
            description="Reviewing authentication and request history requires database connectivity.",
        )

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
    if not _database_available():
        return _render_offline_page(
            "Data Backup",
            description="Exporting or importing backups is paused until the database connection is restored.",
        )

    table_names = [table.name for table in db.Model.metadata.sorted_tables]
    return render_template("admin/data_backup.html", table_names=table_names)


@bp.route("/data-backup/export", methods=["POST"])
@login_required
@require_roles("admin")
def export_data():
    if not _database_available():
        flash("Backups cannot be exported while the database is offline.", "warning")
        return redirect(url_for("admin.data_backup"))

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
    if not _database_available():
        flash("Backups cannot be imported while the database is offline.", "warning")
        return redirect(url_for("admin.data_backup"))

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
    if not _database_available():
        return _render_offline_page(
            "Data Storage Locations",
            description="Update these settings once the database is reachable again.",
        )

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
