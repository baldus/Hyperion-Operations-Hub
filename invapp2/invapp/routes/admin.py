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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urljoin

from flask import (
    Blueprint,
    abort,
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


_DECIMAL_ZERO = Decimal("0")
_DECIMAL_PERCENT = Decimal("100")
_DECIMAL_QUANT = Decimal("0.01")

SQDPM_CATEGORY_DEFINITIONS: dict[str, dict[str, object]] = {
    "Safety": {
        "label": "Safety",
        "icon": "🛡️",
        "description": "Keep teammates protected and equipment ready.",
        "chart_color": "#22c55e",
        "links": (
            {
                "label": "View Incidents",
                "endpoint": "reports.reports_home",
                "icon": "⚠️",
            },
            {
                "label": "Maintenance Board",
                "endpoint": "work.station_overview",
                "icon": "🛠️",
            },
        ),
    },
    "Quality": {
        "label": "Quality",
        "icon": "✅",
        "description": "Monitor complaints, defects, and corrective actions.",
        "chart_color": "#3b82f6",
        "links": (
            {
                "label": "Quality Dashboard",
                "endpoint": "quality.quality_home",
                "icon": "🧪",
            },
            {
                "label": "Production History",
                "endpoint": "production.history",
                "icon": "🏭",
            },
        ),
    },
    "Delivery": {
        "label": "Delivery",
        "icon": "🚚",
        "description": "Track order flow and schedule attainment.",
        "chart_color": "#f97316",
        "links": (
            {
                "label": "Orders Workspace",
                "endpoint": "orders.orders_home",
                "icon": "📦",
            },
            {
                "label": "Production Schedule",
                "endpoint": "production.history",
                "icon": "🗓️",
            },
        ),
    },
    "People": {
        "label": "People",
        "icon": "👥",
        "description": "Support attendance, engagement, and training needs.",
        "chart_color": "#8b5cf6",
        "links": (
            {
                "label": "Attendance & Stations",
                "endpoint": "work.station_overview",
                "icon": "📋",
            },
            {
                "label": "Training & Reports",
                "endpoint": "reports.reports_home",
                "icon": "📈",
            },
        ),
    },
    "Material": {
        "label": "Material",
        "icon": "📦",
        "description": "Ensure material availability and purchasing flow.",
        "chart_color": "#0ea5e9",
        "links": (
            {
                "label": "Inventory Dashboard",
                "endpoint": "inventory.inventory_home",
                "icon": "📦",
            },
            {
                "label": "Open Purchase Requests",
                "endpoint": "purchasing.purchasing_home",
                "icon": "🛒",
            },
        ),
    },
}

SQDPM_CATEGORY_SEQUENCE: tuple[str, ...] = tuple(SQDPM_CATEGORY_DEFINITIONS.keys())


def _slugify_category(category: str) -> str:
    return category.lower().replace("/", "-").replace(" ", "-")


def _normalize_gemba_category(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    for category in SQDPM_CATEGORY_SEQUENCE:
        if text == category.lower():
            return category
    return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP))


def _safe_parse_date(value: str | None, *, field_label: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        flash(f"{field_label} must be provided in YYYY-MM-DD format.", "warning")
        return None


def _resolve_category_links(category: str) -> list[dict[str, object]]:
    definition = SQDPM_CATEGORY_DEFINITIONS.get(category)
    if not definition:
        return []
    resolved: list[dict[str, object]] = []
    for link in definition.get("links", ()):  # type: ignore[assignment]
        endpoint = link.get("endpoint")
        href = link.get("href")
        if endpoint and not href:
            try:
                href = url_for(endpoint)
            except Exception:
                href = None
        if not href:
            continue
        resolved.append(
            {
                "label": link.get("label", "Open"),
                "href": href,
                "icon": link.get("icon"),
                "external": bool(link.get("external")),
            }
        )
    return resolved


def _parse_decimal_field(
    value: str | None, *, field_label: str, required: bool = False
) -> tuple[Decimal | None, str | None]:
    text = (value or "").strip()
    if not text:
        if required:
            return None, f"{field_label} is required."
        return None, None
    try:
        decimal_value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, f"{field_label} must be a valid number."
    return decimal_value.quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP), None


def _gemba_metric_status(
    metric_value: Decimal, target_value: Decimal | None
) -> tuple[str, Decimal | None]:
    if target_value is None or target_value == _DECIMAL_ZERO:
        return "ok", None
    if target_value == _DECIMAL_ZERO:
        return "ok", None
    try:
        ratio = metric_value / target_value
    except (InvalidOperation, ZeroDivisionError):
        return "warn", None
    if ratio >= Decimal("1"):
        status = "ok"
    elif ratio >= Decimal("0.9"):
        status = "warn"
    else:
        status = "alert"
    return status, ratio


def _gemba_metric_form_data(form) -> tuple[dict[str, object] | None, list[str]]:
    errors: list[str] = []
    category_input = form.get("category")
    category = _normalize_gemba_category(category_input)
    if not category:
        errors.append("Select a valid SQDPM category.")

    name = (form.get("metric_name") or "").strip()
    if not name:
        errors.append("Metric name is required.")

    department = (form.get("department") or "").strip() or None

    metric_value, error = _parse_decimal_field(
        form.get("metric_value"), field_label="Metric value", required=True
    )
    if error:
        errors.append(error)

    target_value, error = _parse_decimal_field(
        form.get("target_value"), field_label="Target value", required=False
    )
    if error:
        errors.append(error)

    unit = (form.get("unit") or "").strip() or None

    metric_date = _safe_parse_date(form.get("date"), field_label="Metric date")
    if metric_date is None:
        errors.append("Metric date is required.")

    notes = (form.get("notes") or "").strip() or None
    linked_record_url = (form.get("linked_record_url") or "").strip() or None

    if errors:
        return None, errors

    data = {
        "category": category,
        "metric_name": name,
        "department": department,
        "metric_value": metric_value or _DECIMAL_ZERO,
        "target_value": target_value,
        "unit": unit,
        "date": metric_date,
        "notes": notes,
        "linked_record_url": linked_record_url,
    }
    return data, []


@bp.route("/gemba")
@login_required
@require_roles("admin")
def gemba_dashboard():
    today = date.today()
    range_key = request.args.get("range", "7")
    department_filter = (request.args.get("department") or "").strip() or None
    start_input = request.args.get("start_date")
    end_input = request.args.get("end_date")

    end_date = _safe_parse_date(end_input, field_label="End date") or today

    start_date = None
    if start_input:
        parsed_start = _safe_parse_date(start_input, field_label="Start date")
        if parsed_start is not None:
            start_date = parsed_start
            range_key = "custom"

    if start_date is None:
        if range_key not in {"7", "30"}:
            range_key = "7"
        try:
            days = int(range_key)
        except ValueError:
            days = 7
            range_key = "7"
        start_date = end_date - timedelta(days=max(days - 1, 0))
    else:
        range_key = "custom"

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    department_rows = (
        db.session.query(models.GembaMetric.department)
        .filter(models.GembaMetric.department.isnot(None))
        .filter(models.GembaMetric.department != "")
        .distinct()
        .order_by(models.GembaMetric.department.asc())
        .all()
    )
    departments = [row[0] for row in department_rows if row[0]]

    metric_query = models.GembaMetric.query.filter(
        models.GembaMetric.date >= start_date,
        models.GembaMetric.date <= end_date,
        models.GembaMetric.category.in_(SQDPM_CATEGORY_SEQUENCE),
    )

    if department_filter:
        metric_query = metric_query.filter(
            models.GembaMetric.department == department_filter
        )

    metrics = metric_query.order_by(
        models.GembaMetric.date.desc(), models.GembaMetric.id.desc()
    ).all()

    category_links_map = {
        category: _resolve_category_links(category)
        for category in SQDPM_CATEGORY_SEQUENCE
    }

    category_cards: dict[str, list[dict[str, object]]] = {
        category: [] for category in SQDPM_CATEGORY_SEQUENCE
    }

    category_daily: dict[str, defaultdict[date, dict[str, object]]] = {
        category: defaultdict(
            lambda: {
                "actual_sum": _DECIMAL_ZERO,
                "actual_count": 0,
                "target_sum": _DECIMAL_ZERO,
                "target_count": 0,
            }
        )
        for category in SQDPM_CATEGORY_SEQUENCE
    }

    overview_totals: dict[str, dict[str, object]] = {
        category: {
            "actual_sum": _DECIMAL_ZERO,
            "target_sum": _DECIMAL_ZERO,
            "target_count": 0,
        }
        for category in SQDPM_CATEGORY_SEQUENCE
    }

    for metric in metrics:
        category = _normalize_gemba_category(metric.category)
        if not category:
            continue

        status, ratio = _gemba_metric_status(metric.metric_value, metric.target_value)
        performance_percent = None
        ratio_display = None
        if ratio is not None:
            ratio_display = float(ratio)
            performance_percent = float(
                (ratio * _DECIMAL_PERCENT).quantize(
                    _DECIMAL_QUANT, rounding=ROUND_HALF_UP
                )
            )

        variance = None
        if metric.target_value is not None:
            variance = metric.metric_value - metric.target_value

        metric_links = [dict(link) for link in category_links_map.get(category, [])]
        if metric.linked_record_url:
            external = metric.linked_record_url.startswith(("http://", "https://"))
            metric_links.append(
                {
                    "label": "Linked Record",
                    "href": metric.linked_record_url,
                    "icon": "🔗",
                    "external": external,
                }
            )

        category_cards[category].append(
            {
                "id": metric.id,
                "category": category,
                "name": metric.metric_name,
                "department": metric.department,
                "value": metric.metric_value,
                "target": metric.target_value,
                "unit": metric.unit,
                "date": metric.date,
                "notes": metric.notes,
                "status": status,
                "performance_percent": performance_percent,
                "ratio": ratio_display,
                "variance": variance,
                "links": metric_links,
                "linked_record_url": metric.linked_record_url,
            }
        )

        bucket = category_daily[category][metric.date]
        bucket["actual_sum"] += metric.metric_value
        bucket["actual_count"] += 1
        if metric.target_value is not None:
            bucket["target_sum"] += metric.target_value
            bucket["target_count"] += 1

        totals = overview_totals[category]
        totals["actual_sum"] += metric.metric_value
        if metric.target_value is not None:
            totals["target_sum"] += metric.target_value
            totals["target_count"] += 1

    category_trends: dict[str, dict[str, list[float | None]]] = {}
    for category in SQDPM_CATEGORY_SEQUENCE:
        daily_entries = category_daily[category]
        labels: list[str] = []
        actual_points: list[float | None] = []
        target_points: list[float | None] = []
        for day_key in sorted(daily_entries.keys()):
            entry = daily_entries[day_key]
            actual_avg = None
            if entry["actual_count"]:
                actual_avg = entry["actual_sum"] / entry["actual_count"]
            target_avg = None
            if entry["target_count"]:
                target_avg = entry["target_sum"] / entry["target_count"]
            labels.append(day_key.isoformat())
            actual_points.append(_decimal_to_float(actual_avg))
            target_points.append(_decimal_to_float(target_avg))
        category_trends[category] = {
            "labels": labels,
            "actual": actual_points,
            "target": target_points,
        }

    overview_series: list[dict[str, object]] = []
    for category in SQDPM_CATEGORY_SEQUENCE:
        totals = overview_totals[category]
        actual_total: Decimal = totals["actual_sum"]
        target_total = None
        if totals["target_count"]:
            target_total = totals["target_sum"]
        ratio_value = None
        if target_total not in (None, _DECIMAL_ZERO):
            try:
                ratio_value = actual_total / target_total
            except (InvalidOperation, ZeroDivisionError):
                ratio_value = None
        overview_series.append(
            {
                "category": category,
                "slug": _slugify_category(category),
                "actual": _decimal_to_float(actual_total) or 0.0,
                "target": _decimal_to_float(target_total)
                if target_total is not None
                else None,
                "ratio": float(ratio_value) if ratio_value is not None else None,
                "chart_color": SQDPM_CATEGORY_DEFINITIONS[category]["chart_color"],
            }
        )

    categories = [
        {
            "key": category,
            "slug": _slugify_category(category),
            "label": definition.get("label", category),
            "icon": definition.get("icon"),
            "description": definition.get("description"),
            "chart_color": definition.get("chart_color"),
        }
        for category, definition in SQDPM_CATEGORY_DEFINITIONS.items()
    ]

    has_metrics = any(category_cards[category] for category in SQDPM_CATEGORY_SEQUENCE)

    range_options = (
        ("7", "Last 7 days"),
        ("30", "Last 30 days"),
        ("custom", "Custom range"),
    )

    next_url = request.full_path if request.query_string else request.path

    return render_template(
        "admin/gemba.html",
        categories=categories,
        category_cards=category_cards,
        category_trends=category_trends,
        overview_series=overview_series,
        range_options=range_options,
        selected_range=range_key,
        selected_department=department_filter,
        departments=departments,
        start_date_value=start_date.isoformat(),
        end_date_value=end_date.isoformat(),
        has_metrics=has_metrics,
        total_metrics=len(metrics),
        next_url=next_url,
    )


@bp.route("/gemba/create", methods=["POST"])
@login_required
@require_roles("admin")
def create_gemba_metric():
    data, errors = _gemba_metric_form_data(request.form)
    next_url = request.form.get("next") or url_for("admin.gemba_dashboard")
    if not data or errors:
        for message in errors:
            flash(message, "warning")
        return redirect(next_url)

    metric = models.GembaMetric(**data)
    db.session.add(metric)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("The metric could not be saved. Try again.", "danger")
    else:
        flash("Gemba metric recorded.", "success")

    return redirect(next_url)


@bp.route("/gemba/<int:metric_id>/update", methods=["POST"])
@login_required
@require_roles("admin")
def update_gemba_metric(metric_id: int):
    metric = db.session.get(models.GembaMetric, metric_id)
    if metric is None:
        abort(404)

    data, errors = _gemba_metric_form_data(request.form)
    next_url = request.form.get("next") or url_for("admin.gemba_dashboard")
    if not data or errors:
        for message in errors:
            flash(message, "warning")
        return redirect(next_url)

    for field, value in data.items():
        setattr(metric, field, value)

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Unable to update the metric. Try again.", "danger")
    else:
        flash("Metric updated.", "success")

    return redirect(next_url)


@bp.route("/gemba/<int:metric_id>/delete", methods=["POST"])
@login_required
@require_roles("admin")
def delete_gemba_metric(metric_id: int):
    metric = db.session.get(models.GembaMetric, metric_id)
    if metric is None:
        abort(404)

    db.session.delete(metric)
    next_url = request.form.get("next") or url_for("admin.gemba_dashboard")
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Unable to delete the metric. Try again.", "danger")
    else:
        flash("Metric deleted.", "success")

    return redirect(next_url)


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
            "label": "Gemba / MDI Dashboard",
            "href": url_for("admin.gemba_dashboard") if database_online else None,
            "disabled": not database_online,
            "note": "Review SQDPM performance trends and drill into related modules.",
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
