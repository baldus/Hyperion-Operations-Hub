import gzip
import io
import json
import os
import shlex
import shutil
import secrets
import subprocess
from pathlib import Path
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
from invapp.security import require_roles, require_admin_or_superuser
from invapp.superuser import superuser_required
from invapp.services import backup_service


bp = Blueprint("admin", __name__, url_prefix="/admin")


_AUTOMATED_RECOVERY_ACTION_ID = "automated-recovery"
_CONSOLE_RESTART_ACTION_ID = "console-restart"


_RECOVERY_SEQUENCE = (
    {
        "label": "Refresh apt package index",
        "command": ("sudo", "apt-get", "update"),
        "note": "Fetch the latest package metadata before installing or upgrading components.",
    },
    {
        "label": "Install PostgreSQL server",
        "command": (
            "sudo",
            "apt-get",
            "install",
            "-y",
            "postgresql",
            "postgresql-contrib",
        ),
        "note": "Ensure the database server and extensions are present on the host.",
    },
    {
        "label": "Start PostgreSQL service",
        "command": ("sudo", "systemctl", "start", "postgresql"),
        "note": "Bring PostgreSQL online if it is currently stopped.",
    },
    {
        "label": "Restart PostgreSQL service",
        "command": ("sudo", "systemctl", "restart", "postgresql"),
        "note": "Reload the database service to pick up configuration or package updates.",
    },
    {
        "label": "Ensure application database exists",
        "command": (
            "sudo",
            "-u",
            "postgres",
            "bash",
            "-c",
            "psql -tc \"SELECT 1 FROM pg_database WHERE datname = 'invdb'\" | grep -q 1 || createdb invdb",
        ),
        "note": "Create the expected invdb database when provisioning a new environment.",
    },
    {
        "label": "Ensure application user exists",
        "command": (
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-c",
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inv') THEN CREATE USER inv WITH PASSWORD 'change_me'; END IF; END $$;",
        ),
        "note": "Provision the documented inv role when it is missing.",
    },
    {
        "label": "Ensure database owner",
        "command": (
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-c",
            "ALTER DATABASE invdb OWNER TO inv;",
        ),
        "note": "Guarantee the application role owns the database.",
    },
    {
        "label": "Grant database privileges",
        "command": (
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-c",
            "GRANT ALL PRIVILEGES ON DATABASE invdb TO inv;",
        ),
        "note": "Allow the application role to connect once PostgreSQL is online.",
    },
    {
        "label": "Upgrade console dependencies",
        "command": ("pip", "install", "--upgrade", "-r", "requirements.txt"),
        "note": "Reinstall Python packages inside the active virtual environment.",
    },
    {
        "label": "Capture diagnostics snapshot",
        "command": ("bash", "support/run_diagnostics.sh"),
        "note": "Collect system status information for troubleshooting.",
    },
)


_EMERGENCY_ACTIONS = (
    {
        "id": _AUTOMATED_RECOVERY_ACTION_ID,
        "title": "Automated recovery",
        "button_label": "Diagnose and repair",
        "description": "Run every recovery helper in sequence and surface the results in one place.",
        "note": "Includes package refresh, PostgreSQL maintenance, and a diagnostics snapshot.",
    },
    {
        "id": _CONSOLE_RESTART_ACTION_ID,
        "title": "Restart console services",
        "button_label": "Restart console",
        "description": "Launch the helper script to reload Gunicorn and re-apply configuration.",
        "note": "Invokes start_operations_console.sh on the application host.",
    },
)


_APPROVED_HELPER_SCRIPTS = {
    "start_operations_console.sh",
    "start_inventory.sh",
    "support/run_diagnostics.sh",
}


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
    "support/run_diagnostics.sh",
}


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
        if script_name not in _APPROVED_HELPER_SCRIPTS:
            raise ValueError("Only approved helper scripts may be launched from the console.")

    return parts


def _resolve_helper_script(script_name: str) -> str:
    """Return an absolute path to a bundled helper script."""

    script_path = Path(script_name)
    if script_path.is_absolute():
        if script_path.is_file():
            return str(script_path)
        raise FileNotFoundError(script_name)

    search_roots: list[Path] = []
    cwd = Path.cwd().resolve()
    search_roots.append(cwd)
    parent = cwd.parent
    if parent not in search_roots:
        search_roots.append(parent)

    try:
        app_root = Path(current_app.root_path).resolve()
    except RuntimeError:
        app_root = None

    if app_root is not None:
        for candidate in (app_root, app_root.parent, app_root.parent.parent):
            if candidate not in search_roots:
                search_roots.append(candidate)

    module_root = Path(__file__).resolve()
    for candidate in module_root.parents:
        if candidate not in search_roots:
            search_roots.append(candidate)

    for base in search_roots:
        candidate = (base / script_path).resolve()
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(script_name)


def _normalize_command(parts: tuple[str, ...]) -> tuple[str, ...]:
    if not parts:
        return parts

    normalized = list(parts)

    index = 0
    if normalized[0] == "sudo" and len(normalized) > 1:
        index = 1

    if normalized[index] in {"bash", "sh"} and len(normalized) > index + 1:
        script_name = normalized[index + 1]
        if script_name in _APPROVED_HELPER_SCRIPTS:
            normalized[index + 1] = _resolve_helper_script(script_name)

    return tuple(normalized)


def _run_emergency_command(parts: tuple[str, ...]) -> dict[str, object]:
    resolved_parts = _normalize_command(parts)
    completed = subprocess.run(
        resolved_parts,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return {
        "command": _quote_command(resolved_parts),
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def _run_recovery_sequence() -> dict[str, object]:
    steps: list[dict[str, object]] = []
    failures = 0

    for step in _RECOVERY_SEQUENCE:
        result = _run_emergency_command(step["command"])
        result["label"] = step["label"]
        result["note"] = step.get("note")
        steps.append(result)
        if result["exit_code"] != 0:
            failures += 1

    total = len(steps)
    summary = f"{total - failures} of {total} steps succeeded" if total else "No recovery steps were executed."

    return {
        "label": "Automated recovery sequence",
        "exit_code": 0 if failures == 0 else 1,
        "note": summary,
        "steps": steps,
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

    error_reports: list[models.ErrorReport] = []
    if database_online:
        error_reports = (
            models.ErrorReport.query.order_by(models.ErrorReport.occurred_at.desc())
            .limit(10)
            .all()
        )

    return render_template(
        "admin/tools.html",
        system_health=system_health,
        quick_links=quick_links,
        database_online=database_online,
        error_reports=error_reports,
    )


@bp.route("/emergency-console", methods=["GET", "POST"])
@login_required
@require_roles("admin")
def emergency_console():
    actions = _EMERGENCY_ACTIONS
    action_lookup = {action["id"]: action for action in actions}
    command_result: dict[str, object] | None = None
    error_message: str | None = None
    selected_action_id: str | None = None
    custom_command = (request.form.get("custom_command") or "").strip()

    if request.method == "POST":
        action_id = (request.form.get("action_id") or "").strip()
        try:
            if action_id:
                action = action_lookup.get(action_id)
                if not action:
                    raise ValueError("Unknown action requested.")

                selected_action_id = action_id

                if action_id == _AUTOMATED_RECOVERY_ACTION_ID:
                    command_result = _run_recovery_sequence()
                elif action_id == _CONSOLE_RESTART_ACTION_ID:
                    command_result = _run_emergency_command(("bash", "start_operations_console.sh"))
                    command_result["label"] = action["title"]
                    if action.get("note"):
                        command_result["note"] = action["note"]
                else:
                    raise ValueError("Unknown action requested.")
            elif custom_command:
                parts = _validate_custom_command(custom_command)
                command_result = _run_emergency_command(parts)
                command_result["label"] = "Custom command"
            else:
                raise ValueError("Run an automated action or enter a custom command to continue.")
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
        actions=actions,
        command_result=command_result,
        error_message=error_message,
        selected_action_id=selected_action_id,
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


@bp.route("/settings/backups", methods=["GET", "POST"])
@login_required
@require_admin_or_superuser
def backup_settings():
    current_frequency = backup_service.get_backup_frequency_hours(current_app)
    default_frequency = backup_service.DEFAULT_BACKUP_FREQUENCY_HOURS

    if request.method == "POST":
        raw_value = (request.form.get("backup_frequency_hours") or "").strip()
        try:
            frequency = int(raw_value)
        except (TypeError, ValueError):
            flash("Backup frequency must be a whole number of hours.", "warning")
            return redirect(url_for("admin.backup_settings"))

        if frequency <= 0:
            flash("Backup frequency must be greater than zero hours.", "warning")
            return redirect(url_for("admin.backup_settings"))

        try:
            backup_service.update_backup_frequency_hours(frequency)
        except SQLAlchemyError as exc:
            db.session.rollback()
            current_app.logger.exception("Failed to update backup frequency: %s", exc)
            flash("Unable to save backup settings. Please try again.", "danger")
            return redirect(url_for("admin.backup_settings"))

        backup_service.refresh_backup_schedule(current_app, force=True)
        flash("Backup schedule updated.", "success")
        return redirect(url_for("admin.backup_settings"))

    return render_template(
        "admin/backup_settings.html",
        backup_frequency_hours=current_frequency,
        default_frequency_hours=default_frequency,
    )


def _backup_restore_csrf_token() -> str:
    token = session.get("backup_restore_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["backup_restore_csrf"] = token
    return token


def _record_backup_restore_event(
    *,
    filename: str,
    status: str,
    action: str,
    message: str | None = None,
) -> None:
    try:
        event = models.BackupRestoreEvent(
            user_id=getattr(current_user, "id", None),
            username=getattr(current_user, "username", None),
            backup_filename=filename,
            action=action,
            status=status,
            message=message,
        )
        db.session.add(event)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to record backup restore event.")


@bp.route("/backups", methods=["GET"])
@superuser_required
def backups_home():
    backup_dir = None
    backups = []
    restore_allowed = os.environ.get("ALLOW_RESTORE") == "1"
    message = None

    try:
        backup_dir = backup_service.get_backup_dir(current_app)
        backups = backup_service.list_backup_files(backup_dir)
    except Exception as exc:
        current_app.logger.exception("Failed to list backups: %s", exc)
        message = "Backup storage is unavailable. Check BACKUP_DIR permissions."

    return render_template(
        "admin/backups.html",
        backups=backups,
        backup_dir=str(backup_dir) if backup_dir else None,
        restore_allowed=restore_allowed,
        csrf_token=_backup_restore_csrf_token(),
        message=message,
    )


@bp.route("/backups/restore", methods=["POST"])
@superuser_required
def restore_backup():
    restore_allowed = os.environ.get("ALLOW_RESTORE") == "1"
    if not restore_allowed:
        flash("Restore is disabled. Set ALLOW_RESTORE=1 to enable this action.", "warning")
        _record_backup_restore_event(
            filename=request.form.get("backup_filename", "unknown"),
            status="failed",
            action="restore",
            message="Restore disabled by ALLOW_RESTORE.",
        )
        return redirect(url_for("admin.backups_home"))

    token = request.form.get("csrf_token")
    if not token or token != session.get("backup_restore_csrf"):
        flash("Invalid restore request. Please try again.", "danger")
        return redirect(url_for("admin.backups_home"))

    filename = (request.form.get("backup_filename") or "").strip()
    confirmation = (request.form.get("confirm_restore") or "").strip().upper()
    acknowledged = request.form.get("confirm_ack") == "yes"
    if not acknowledged:
        flash("Please confirm the restore acknowledgement checkbox.", "warning")
        return redirect(url_for("admin.backups_home"))
    if confirmation != "RESTORE":
        flash("Type RESTORE to confirm the backup restore.", "warning")
        return redirect(url_for("admin.backups_home"))

    if not backup_service.is_valid_backup_filename(filename):
        _record_backup_restore_event(
            filename=filename or "unknown",
            status="failed",
            action="restore",
            message="Invalid backup filename.",
        )
        flash("Invalid backup filename selected.", "danger")
        return redirect(url_for("admin.backups_home"))

    _record_backup_restore_event(
        filename=filename,
        status="started",
        action="restore",
        message="Restore initiated.",
    )

    try:
        logger = current_app.logger
        result_message = backup_service.restore_database_backup(current_app, filename, logger)
    except (ValueError, FileNotFoundError) as exc:
        _record_backup_restore_event(
            filename=filename,
            status="failed",
            action="restore",
            message=str(exc),
        )
        flash(str(exc), "danger")
        return redirect(url_for("admin.backups_home"))
    except Exception as exc:
        current_app.logger.exception("Restore failed: %s", exc)
        _record_backup_restore_event(
            filename=filename,
            status="failed",
            action="restore",
            message=str(exc),
        )
        flash("Restore failed. Check logs for details.", "danger")
        return redirect(url_for("admin.backups_home"))

    _record_backup_restore_event(
        filename=filename,
        status="succeeded",
        action="restore",
        message=result_message,
    )
    flash("Restore completed. Restart the console if needed.", "success")
    return redirect(url_for("admin.backups_home"))


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


_IMPORT_BATCH_SIZE = 500


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
        stream = _open_backup_stream(upload)
        with stream:
            raw_data = json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        flash("The uploaded file is not a valid backup.", "danger")
        return redirect(url_for("admin.data_backup"))

    metadata = db.Model.metadata

    try:
        for table in reversed(metadata.sorted_tables):
            db.session.execute(table.delete())

        for table in metadata.sorted_tables:
            table_name = table.name
            rows = raw_data.get(table_name, [])
            if not rows:
                continue

            prepared_rows = _prepare_table_rows(table, rows)
            for batch in _batched(prepared_rows, _IMPORT_BATCH_SIZE):
                db.session.execute(table.insert(), batch)

            raw_data.pop(table_name, None)

        db.session.commit()
    except Exception as exc:  # pragma: no cover - defensive rollback
        db.session.rollback()
        current_app.logger.exception("Failed to import backup: %s", exc)
        flash("Import failed. No changes were applied.", "danger")
        return redirect(url_for("admin.data_backup"))

    flash("Backup imported successfully.", "success")
    return redirect(url_for("admin.data_backup"))


def _open_backup_stream(upload):
    """Return a text stream for a JSON or gzipped backup upload."""

    stream = upload.stream
    stream.seek(0)
    magic = stream.read(2)
    stream.seek(0)

    if magic == b"\x1f\x8b":
        gzip_file = gzip.GzipFile(fileobj=stream)
        return io.TextIOWrapper(gzip_file, encoding="utf-8")

    return io.TextIOWrapper(stream, encoding="utf-8")


def _batched(iterable, batch_size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _prepare_table_rows(table, rows):
    for row in rows:
        yield {
            column.name: _parse_value(column, row.get(column.name))
            for column in table.columns
        }


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
        (
            "Item Shortage Attachments",
            current_app.config.get("PURCHASING_ATTACHMENT_UPLOAD_FOLDER"),
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
