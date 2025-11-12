import gzip
import io
import json
import os
import shlex
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from datetime import date, datetime, time as time_type, timedelta
from decimal import Decimal, InvalidOperation
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

from werkzeug.routing import BuildError

from invapp import models
from invapp.extensions import db
from invapp.login import current_user, login_required, logout_user
from invapp.offline import is_emergency_mode_active
from invapp.security import require_roles


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


_GEMBA_QUANTIZE = Decimal("0.0001")


def _parse_decimal_field(
    value: str | None,
    *,
    field_label: str,
    allow_empty: bool = False,
) -> tuple[Decimal | None, str | None]:
    text = (value or "").strip()
    if not text:
        if allow_empty:
            return None, None
        return None, f"{field_label} is required."

    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, f"{field_label} must be a valid number."

    try:
        return number.quantize(_GEMBA_QUANTIZE), None
    except InvalidOperation:
        return number, None


def _format_decimal_for_input(value: Decimal | float | int | None) -> str:
    if value is None:
        return ""

    if not isinstance(value, Decimal):
        try:
            value = Decimal(value)
        except (InvalidOperation, ValueError):
            return str(value)

    normalized = value.quantize(_GEMBA_QUANTIZE).normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_date_field(value: str | None, *, default: date | None = None) -> tuple[date | None, str | None]:
    text = (value or "").strip()
    if not text:
        return default, None

    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), None
    except ValueError:
        return default, "Dates must use the YYYY-MM-DD format."


def _metric_status_class(actual: Decimal | None, target: Decimal | None) -> str:
    if actual is None:
        return "status-warn"
    if target in (None, Decimal(0)):
        return "status-ok"

    if actual >= target:
        return "status-ok"
    if target == 0:
        return "status-ok"
    ninety_percent = target * Decimal("0.90")
    if actual >= ninety_percent:
        return "status-warn"
    return "status-alert"


def _metric_delta_label(actual: Decimal | None, target: Decimal | None) -> str | None:
    if actual is None or target is None:
        return None

    delta = actual - target
    if delta == 0:
        return "On target"

    formatted = _format_decimal_for_input(delta)
    if not formatted:
        return None

    sign = "+" if delta > 0 else ""
    return f"{sign}{formatted}"


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redirect_to_gemba_dashboard():
    next_target = (request.form.get("next") or "").strip()
    if next_target:
        host_url = request.host_url
        absolute_target = urljoin(host_url, next_target)
        if absolute_target.startswith(host_url):
            return redirect(next_target)
    return redirect(url_for("admin.gemba_dashboard"))


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


def _category_shortcuts() -> dict[str, list[dict[str, str]]]:
    """Resolve related-module shortcuts for each SQDPM category."""

    raw_shortcuts: dict[str, list[dict[str, str]]] = {
        "Safety": [
            {"label": "MDI Reports", "endpoint": "reports.reports_home"},
            {"label": "Admin Tools", "endpoint": "admin.tools"},
        ],
        "Quality": [
            {"label": "Quality Dashboard", "endpoint": "quality.quality_home"},
        ],
        "Delivery": [
            {"label": "Orders Workspace", "endpoint": "orders.orders_home"},
            {"label": "Production History", "endpoint": "production.history"},
        ],
        "People": [
            {"label": "Workstations Overview", "endpoint": "work.station_overview"},
        ],
        "Material": [
            {"label": "Inventory Dashboard", "endpoint": "inventory.inventory_home"},
            {"label": "Purchase Requests", "endpoint": "purchasing.purchasing_home"},
        ],
    }

    shortcuts: dict[str, list[dict[str, str]]] = {}
    for category, entries in raw_shortcuts.items():
        resolved: list[dict[str, str]] = []
        for entry in entries:
            endpoint = entry.get("endpoint")
            if not endpoint:
                continue
            try:
                href = url_for(endpoint)
            except BuildError:
                current_app.logger.debug(
                    "Skipping shortcut for %s because endpoint %s is unavailable.",
                    category,
                    endpoint,
                )
                continue
            resolved.append({"label": entry["label"], "href": href})
        shortcuts[category] = resolved

    return shortcuts


def _category_definitions() -> dict[str, str]:
    defaults = models.GembaCategory.DEFAULT_DEFINITIONS
    definitions: dict[str, str] = {name: text for name, text in defaults.items()}
    for category in models.GembaCategory.query.order_by(models.GembaCategory.name).all():
        if category.description:
            definitions[category.name] = category.description
    return definitions


@bp.route("/gemba")
@login_required
@require_roles("admin")
def gemba_dashboard():
    if not _database_available():
        return _render_offline_page(
            "Gemba / MDI",
            description="Reviewing SQDPM metrics requires database connectivity.",
        )

    models.GembaCategory.ensure_defaults()

    department_rows = (
        db.session.query(models.GembaMetric.department)
        .filter(models.GembaMetric.department.isnot(None))
        .filter(models.GembaMetric.department != "")
        .distinct()
        .order_by(models.GembaMetric.department.asc())
        .all()
    )
    departments = [row[0] for row in department_rows if row[0]]

    selected_department = (request.args.get("department") or "").strip()

    today = date.today()
    default_start = today - timedelta(days=29)
    default_end = today

    start_date, start_error = _parse_date_field(
        request.args.get("start_date"), default=default_start
    )
    end_date, end_error = _parse_date_field(request.args.get("end_date"), default=default_end)

    redirect_args: dict[str, str] = {}
    if selected_department:
        redirect_args["department"] = selected_department

    if start_error:
        flash(start_error, "warning")
    if end_error:
        flash(end_error, "warning")
    if start_error or end_error:
        return redirect(url_for("admin.gemba_dashboard", **redirect_args))

    start_date = start_date or default_start
    end_date = end_date or default_end

    if start_date > end_date:
        flash("The start date must be on or before the end date.", "warning")
        return redirect(url_for("admin.gemba_dashboard", **redirect_args))

    metrics_query = models.GembaMetric.query.filter(
        models.GembaMetric.date >= start_date,
        models.GembaMetric.date <= end_date,
    )
    if selected_department:
        metrics_query = metrics_query.filter(
            models.GembaMetric.department == selected_department
        )

    metrics = (
        metrics_query.order_by(
            models.GembaMetric.date.desc(), models.GembaMetric.metric_name.asc()
        ).all()
    )

    if selected_department and selected_department not in departments:
        departments.append(selected_department)
        departments.sort(key=lambda value: value.lower())

    categories = models.GembaMetric.CATEGORIES
    grouped: dict[str, dict[str, list[models.GembaMetric]]] = {
        category: defaultdict(list) for category in categories
    }
    trend_data: dict[str, list[dict[str, object]]] = {category: [] for category in categories}

    for metric in metrics:
        category = metric.category or categories[0]
        if category not in grouped:
            grouped[category] = defaultdict(list)
            trend_data.setdefault(category, [])
        department_label = metric.department or "All Departments"
        grouped[category][department_label].append(metric)
        trend_data.setdefault(category, []).append(
            {
                "date": metric.date.isoformat(),
                "actual": _decimal_to_float(metric.metric_value),
                "target": _decimal_to_float(metric.target_value),
                "department": department_label,
                "metric": metric.metric_name,
            }
        )

    for entries in trend_data.values():
        entries.sort(key=lambda item: item["date"])

    category_metrics: dict[str, list[dict[str, object]]] = {}
    overview_data: dict[str, dict[str, float | None]] = {}

    for category in categories:
        department_sections: list[dict[str, object]] = []
        category_metrics_list = []
        department_map = grouped.get(category, {})

        for department_label in sorted(department_map.keys(), key=lambda value: value.lower()):
            metrics_for_department = sorted(
                department_map[department_label],
                key=lambda item: (item.date, item.metric_name.lower()),
                reverse=True,
            )

            metric_cards: list[dict[str, object]] = []
            for metric in metrics_for_department:
                actual = metric.metric_value
                target = metric.target_value
                ratio_display = None
                if actual is not None and target not in (None, Decimal(0)):
                    try:
                        ratio_value = (actual / target) * Decimal(100)
                        ratio_display = f"{_format_decimal_for_input(ratio_value)}%"
                    except (InvalidOperation, ZeroDivisionError):
                        ratio_display = None

                metric_cards.append(
                    {
                        "id": metric.id,
                        "name": metric.metric_name,
                        "value_display": _format_decimal_for_input(actual) or "0",
                        "target_display": _format_decimal_for_input(target),
                        "unit": metric.unit or "",
                        "date_display": metric.date.strftime("%b %d, %Y"),
                        "date_iso": metric.date.isoformat(),
                        "notes": metric.notes or "",
                        "linked_record_url": metric.linked_record_url or "",
                        "status_class": _metric_status_class(actual, target),
                        "delta_label": _metric_delta_label(actual, target),
                        "ratio_display": ratio_display,
                        "links": [
                            {"label": link.label, "href": link.url}
                            for link in getattr(metric, "links", [])
                        ],
                        "raw": {
                            "metric_name": metric.metric_name,
                            "metric_value": _format_decimal_for_input(actual),
                            "target_value": _format_decimal_for_input(target),
                            "unit": metric.unit or "",
                            "department": metric.department or "",
                            "category": metric.category,
                            "date": metric.date.isoformat(),
                            "notes": metric.notes or "",
                            "linked_record_url": metric.linked_record_url or "",
                        },
                    }
                )

            department_sections.append(
                {
                    "department": department_label,
                    "metrics": metric_cards,
                }
            )
            category_metrics_list.extend(metrics_for_department)

        category_metrics[category] = department_sections

        actual_values = [metric.metric_value for metric in category_metrics_list if metric.metric_value is not None]
        target_values = [
            metric.target_value for metric in category_metrics_list if metric.target_value is not None
        ]

        actual_average: Decimal | None = None
        target_average: Decimal | None = None
        if actual_values:
            actual_average = sum(actual_values, Decimal(0)) / Decimal(len(actual_values))
        if target_values:
            target_average = sum(target_values, Decimal(0)) / Decimal(len(target_values))

        overview_data[category] = {
            "actual": _decimal_to_float(actual_average),
            "target": _decimal_to_float(target_average),
        }

    active_category = next(
        (category for category in categories if category_metrics.get(category)),
        categories[0],
    )

    return render_template(
        "admin/gemba.html",
        categories=categories,
        category_metrics=category_metrics,
        category_definitions=_category_definitions(),
        category_shortcuts=_category_shortcuts(),
        departments=sorted(departments, key=lambda value: value.lower()),
        selected_department=selected_department,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        trend_data=trend_data,
        overview_data=overview_data,
        active_category=active_category,
        total_metrics=len(metrics),
    )


@bp.route("/gemba/metrics", methods=["POST"])
@login_required
@require_roles("admin")
def create_gemba_metric():
    if not _database_available():
        flash("Database connectivity is required to record Gemba metrics.", "warning")
        return redirect(url_for("admin.gemba_dashboard"))

    form_category = request.form.get("category")
    try:
        category = models.GembaMetric.normalize_category(form_category)
    except ValueError:
        flash("Select a valid SQDPM category for the metric.", "warning")
        return _redirect_to_gemba_dashboard()

    metric_name = (request.form.get("metric_name") or "").strip()
    if not metric_name:
        flash("Provide a name for the metric.", "warning")
        return _redirect_to_gemba_dashboard()

    metric_value, value_error = _parse_decimal_field(
        request.form.get("metric_value"), field_label="Metric value"
    )
    target_value, target_error = _parse_decimal_field(
        request.form.get("target_value"), field_label="Target value", allow_empty=True
    )
    metric_date, date_error = _parse_date_field(
        request.form.get("date"), default=date.today()
    )

    errors = [message for message in (value_error, target_error, date_error) if message]
    for message in errors:
        flash(message, "warning")
    if errors:
        return _redirect_to_gemba_dashboard()

    metric = models.GembaMetric(
        category=category,
        department=(request.form.get("department") or "").strip() or None,
        metric_name=metric_name,
        metric_value=metric_value,
        target_value=target_value,
        unit=(request.form.get("unit") or "").strip() or None,
        date=metric_date or date.today(),
        notes=(request.form.get("notes") or "").strip() or None,
        linked_record_url=(request.form.get("linked_record_url") or "").strip() or None,
    )

    db.session.add(metric)
    db.session.commit()
    flash("Gemba metric recorded.", "success")
    return _redirect_to_gemba_dashboard()


@bp.route("/gemba/metrics/<int:metric_id>/update", methods=["POST"])
@login_required
@require_roles("admin")
def update_gemba_metric(metric_id: int):
    if not _database_available():
        flash("Updating metrics requires database connectivity.", "warning")
        return redirect(url_for("admin.gemba_dashboard"))

    metric = models.GembaMetric.query.get_or_404(metric_id)

    form_category = request.form.get("category")
    try:
        metric.category = models.GembaMetric.normalize_category(form_category)
    except ValueError:
        flash("Select a valid SQDPM category for the metric.", "warning")
        return _redirect_to_gemba_dashboard()

    metric.metric_name = (request.form.get("metric_name") or "").strip()
    if not metric.metric_name:
        flash("Metric name cannot be empty.", "warning")
        return _redirect_to_gemba_dashboard()

    metric_value, value_error = _parse_decimal_field(
        request.form.get("metric_value"), field_label="Metric value"
    )
    target_value, target_error = _parse_decimal_field(
        request.form.get("target_value"), field_label="Target value", allow_empty=True
    )
    metric_date, date_error = _parse_date_field(
        request.form.get("date"), default=metric.date
    )

    errors = [message for message in (value_error, target_error, date_error) if message]
    for message in errors:
        flash(message, "warning")
    if errors:
        return _redirect_to_gemba_dashboard()

    metric.metric_value = metric_value
    metric.target_value = target_value
    metric.date = metric_date or metric.date
    metric.unit = (request.form.get("unit") or "").strip() or None
    metric.department = (request.form.get("department") or "").strip() or None
    metric.notes = (request.form.get("notes") or "").strip() or None
    metric.linked_record_url = (request.form.get("linked_record_url") or "").strip() or None

    db.session.commit()
    flash("Gemba metric updated.", "success")
    return _redirect_to_gemba_dashboard()


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
            "label": "Gemba / MDI Dashboard",
            "href": url_for("admin.gemba_dashboard"),
            "disabled": False,
            "note": "Review daily SQDPM performance and trends.",
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
