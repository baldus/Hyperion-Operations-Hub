from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Tuple

from flask import flash, redirect, render_template, request, url_for

from invapp.extensions import db
from invapp.mdi.models import CATEGORY_DISPLAY, CategoryMetric, MDIEntry, STATUS_BADGES


CATEGORY_SEQUENCE = ["Safety", "Quality", "Delivery", "People", "Materials"]
CATEGORY_ENDPOINTS = {
    "Safety": "mdi.safety_dashboard",
    "Quality": "mdi.quality_dashboard",
    "Delivery": "mdi.delivery_dashboard",
    "People": "mdi.people_dashboard",
    "Materials": "mdi.materials_dashboard",
}


CATEGORY_COLORS = {
    "Safety": "#dc3545",
    "Quality": "#0d6efd",
    "Delivery": "#198754",
    "People": "#fd7e14",
    "Materials": "#6c757d",
}


CATEGORY_METRIC_CONFIG = {
    "Safety": {
        "title": "Safety Metrics",
        "subtitle": "Capture incidents and observation counts without logging new cards.",
        "metrics": [
            {"value": "Incidents", "label": "Incidents"},
            {"value": "Observations", "label": "Observations"},
        ],
        "value_label": "Count",
        "allow_target": False,
        "unit": None,
    },
    "Quality": {
        "title": "Quality Metrics",
        "subtitle": "Maintain notice and observation tallies used in the charts above.",
        "metrics": [
            {"value": "Notices", "label": "Quality Notices"},
            {"value": "Observations", "label": "Quality Observations"},
        ],
        "value_label": "Count",
        "allow_target": False,
        "unit": None,
    },
    "Delivery": {
        "title": "Delivery Metrics",
        "subtitle": "Record daily production output without creating follow-up actions.",
        "metrics": [
            {"value": "Production Output", "label": "Production Output"},
        ],
        "value_label": "Units",
        "allow_target": True,
        "target_placeholder": "Optional",
        "unit": "units",
    },
    "People": {
        "title": "Attendance Metrics",
        "subtitle": "Track attendance by zone for morning discussions.",
        "metrics": [
            {"value": "Attendance", "label": "Attendance"},
        ],
        "value_label": "Employees",
        "dimension_label": "Zone",
        "dimension_placeholder": "e.g., Gates",
        "allow_target": False,
        "unit": "employees",
        "default_dimensions": ["Gates", "Electronics"],
    },
}


def safety_dashboard():  # pragma: no cover - registered via blueprint
    category = "Safety"
    if request.method == "POST" and _handle_metric_submission(category):
        return redirect(request.path)

    entries = _entries_for_category(category)
    date_range, labels = _chart_date_range()

    incidents_data = _series_for_metric(category, "Incidents", date_range)
    observations_data = _series_for_metric(category, "Observations", date_range)

    charts = [
        {
            "id": "safety-incidents-chart",
            "title": "Incidents Over Time",
            "subtitle": "Recordable incidents logged each day",
            "column_class": "col-12 col-xl-6",
            "config": _line_chart(
                labels,
                [
                    _dataset(
                        label="Incidents",
                        data=incidents_data,
                        color=CATEGORY_COLORS[category],
                    )
                ],
            ),
        },
        {
            "id": "safety-observations-chart",
            "title": "Observations Over Time",
            "subtitle": "Safety observations completed daily",
            "column_class": "col-12 col-xl-6",
            "config": _line_chart(
                labels,
                [
                    _dataset(
                        label="Observations",
                        data=observations_data,
                        color=CATEGORY_COLORS[category],
                        border_alpha=0.7,
                        background_alpha=0.15,
                    )
                ],
            ),
        },
    ]

    context = _base_context(category, "Track recordable incidents and safety walk observations.")
    context.update({
        "charts": charts,
        "entries": entries,
        **_metric_context(category),
    })
    return render_template("safety.html", **context)


def quality_dashboard():  # pragma: no cover - registered via blueprint
    category = "Quality"
    if request.method == "POST" and _handle_metric_submission(category):
        return redirect(request.path)

    entries = _entries_for_category(category)
    date_range, labels = _chart_date_range()

    notices_data = _series_for_metric(category, "Notices", date_range)
    observations_data = _series_for_metric(category, "Observations", date_range)

    charts = [
        {
            "id": "quality-trends-chart",
            "title": "Quality Notices vs Observations",
            "subtitle": "High priority notices compared to other observations",
            "column_class": "col-12 col-xl-8",
            "config": _line_chart(
                labels,
                [
                    _dataset(
                        label="Notices",
                        data=notices_data,
                        color=CATEGORY_COLORS[category],
                    ),
                    _dataset(
                        label="Observations",
                        data=observations_data,
                        color=CATEGORY_COLORS[category],
                        border_alpha=0.65,
                        background_alpha=0.2,
                    ),
                ],
            ),
        }
    ]

    context = _base_context(category, "Monitor quality notices alongside corrective observations.")
    context.update({
        "charts": charts,
        "entries": entries,
        **_metric_context(category),
    })
    return render_template("quality.html", **context)


def delivery_dashboard():  # pragma: no cover - registered via blueprint
    category = "Delivery"
    if request.method == "POST" and _handle_metric_submission(category):
        return redirect(request.path)

    entries = _entries_for_category(category)
    date_range, labels = _chart_date_range()

    production_totals = _series_for_metric(category, "Production Output", date_range)

    charts = [
        {
            "id": "delivery-output-chart",
            "title": "Production Output by Day",
            "subtitle": "Units completed per production day",
            "column_class": "col-12 col-xl-8",
            "config": _bar_chart(
                labels,
                [
                    _dataset(
                        label="Output", data=production_totals, color=CATEGORY_COLORS[category]
                    )
                ],
            ),
        }
    ]

    context = _base_context(category, "Daily production results aligned to customer delivery needs.")
    context.update({
        "charts": charts,
        "entries": entries,
        **_metric_context(category),
    })
    return render_template("delivery.html", **context)


def people_dashboard():  # pragma: no cover - registered via blueprint
    category = "People"
    if request.method == "POST" and _handle_metric_submission(category):
        return redirect(request.path)

    entries = _entries_for_category(category)
    date_range, labels = _chart_date_range()

    grouped_attendance = _series_for_metric_by_dimension(
        category,
        "Attendance",
        date_range,
    )

    zone_colors = ["#fd7e14", "#ffc107", "#ffa94d", "#ff922b"]
    datasets = []
    for index, (zone, values) in enumerate(grouped_attendance.items()):
        datasets.append(
            _dataset(
                label=zone,
                data=values,
                color=zone_colors[index % len(zone_colors)],
                is_bar=True,
            )
        )

    charts = [
        {
            "id": "people-attendance-chart",
            "title": "Attendance by Zone",
            "subtitle": "Employee attendance split by operating zone",
            "column_class": "col-12 col-xl-8",
            "config": _stacked_bar_chart(labels, datasets),
        }
    ]

    context = _base_context(category, "Visualize staffing coverage across work zones.")
    context.update({
        "charts": charts,
        "entries": entries,
        **_metric_context(category),
    })
    return render_template("people.html", **context)


def materials_dashboard():  # pragma: no cover - registered via blueprint
    category = "Materials"
    context = _base_context(category, "Track material shortages and follow-up actions.")
    context.update({
        "materials_summary_url": url_for("mdi.api_materials_summary"),
        "item_shortages_url": url_for("purchasing.purchasing_home"),
    })
    return render_template("materials.html", **context)


def _base_context(category: str, subtitle: str) -> Dict[str, object]:
    return {
        "category": category,
        "category_color": CATEGORY_COLORS.get(category, "#0d6efd"),
        "category_meta": CATEGORY_DISPLAY,
        "status_badges": STATUS_BADGES,
        "subtitle": subtitle,
        "nav_links": _nav_links(category),
    }


def _entries_for_category(category: str) -> List[MDIEntry]:
    return (
        MDIEntry.query.filter(MDIEntry.category == category)
        .order_by(MDIEntry.date_logged.desc(), MDIEntry.created_at.desc())
        .all()
    )


def _chart_date_range(days: int = 14) -> Tuple[List[date], List[str]]:
    today = date.today()
    dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    labels = [day.strftime("%b %d") for day in dates]
    return dates, labels


def _metric_context(category: str) -> Dict[str, object]:
    metric_config = CATEGORY_METRIC_CONFIG.get(category)
    return {
        "metric_config": metric_config,
        "metric_entries": _recent_metrics(category) if metric_config else [],
        "metric_default_date": date.today().isoformat(),
    }


def _build_daily_counts(
    entries: Iterable[MDIEntry],
    date_range: Iterable[date],
    predicate: Callable[[MDIEntry], bool],
) -> List[int]:
    counts = {day: 0 for day in date_range}
    for entry in entries:
        if entry.date_logged in counts and predicate(entry):
            counts[entry.date_logged] += 1
    return [counts[day] for day in date_range]


def _series_for_metric(category: str, metric_name: str, date_range: Iterable[date]) -> List[float]:
    totals = {day: 0.0 for day in date_range}
    metrics = (
        CategoryMetric.query.filter(CategoryMetric.category == category)
        .filter(CategoryMetric.metric_name == metric_name)
        .filter(CategoryMetric.recorded_date >= date_range[0])
        .filter(CategoryMetric.recorded_date <= date_range[-1])
        .all()
    )

    for metric in metrics:
        if metric.recorded_date in totals:
            totals[metric.recorded_date] += float(metric.value or 0)

    return [round(totals[day], 2) for day in date_range]


def _series_for_metric_by_dimension(
    category: str, metric_name: str, date_range: Iterable[date]
) -> Dict[str, List[float]]:
    template = {day: 0.0 for day in date_range}
    grouped: Dict[str, Dict[date, float]] = defaultdict(lambda: template.copy())

    metrics = (
        CategoryMetric.query.filter(CategoryMetric.category == category)
        .filter(CategoryMetric.metric_name == metric_name)
        .filter(CategoryMetric.recorded_date >= date_range[0])
        .filter(CategoryMetric.recorded_date <= date_range[-1])
        .all()
    )

    for metric in metrics:
        if metric.recorded_date in template:
            key = (metric.dimension or metric.metric_name).strip() or metric.metric_name
            grouped[key][metric.recorded_date] += float(metric.value or 0)

    defaults = CATEGORY_METRIC_CONFIG.get(category, {}).get("default_dimensions", [])
    if not grouped and defaults:
        for dimension in defaults:
            grouped[dimension] = template.copy()

    ordered: Dict[str, List[float]] = {}
    seen = set()
    for key in defaults:
        if key in grouped:
            ordered[key] = [round(grouped[key][day], 2) for day in date_range]
            seen.add(key)

    for key in sorted(grouped):
        if key in seen:
            continue
        ordered[key] = [round(grouped[key][day], 2) for day in date_range]
    return ordered


def _recent_metrics(category: str, limit: int = 20) -> List[CategoryMetric]:
    return (
        CategoryMetric.query.filter(CategoryMetric.category == category)
        .order_by(CategoryMetric.recorded_date.desc(), CategoryMetric.created_at.desc())
        .limit(limit)
        .all()
    )


def _extract_metric_fields(metric_config: Dict[str, object], form) -> Dict[str, object]:
    metric_name = (form.get("metric_name") or "").strip()
    valid_metrics = {option["value"] for option in metric_config.get("metrics", [])}
    if metric_name not in valid_metrics:
        raise ValueError("Select a valid metric option before submitting.")

    try:
        recorded_date = date.fromisoformat(form.get("recorded_date", ""))
    except ValueError as error:
        raise ValueError("Enter a valid date for the metric entry.") from error

    try:
        value = float(form.get("value", ""))
    except (TypeError, ValueError) as error:
        raise ValueError("Provide a numeric value for the metric entry.") from error

    dimension = None
    if metric_config.get("dimension_label"):
        dimension = (form.get("dimension") or "").strip()
        if not dimension:
            raise ValueError(f"Enter a {metric_config['dimension_label']} for this metric.")

    target_value = None
    if metric_config.get("allow_target"):
        target_raw = (form.get("target") or "").strip()
        if target_raw:
            try:
                target_value = float(target_raw)
            except (TypeError, ValueError) as error:
                raise ValueError("Target must be numeric when provided.") from error

    return {
        "metric_name": metric_name,
        "recorded_date": recorded_date,
        "value": value,
        "dimension": dimension,
        "target": target_value,
        "unit": metric_config.get("unit"),
    }


def _handle_metric_submission(category: str) -> bool:
    metric_config = CATEGORY_METRIC_CONFIG.get(category)
    if not metric_config:
        flash("This category does not accept metric updates.", "warning")
        return False

    form_id = (request.form.get("form_id") or "add_metric").strip()

    if form_id == "bulk_delete_metrics":
        metric_ids = request.form.getlist("metric_ids")
        if not metric_ids:
            flash("Select at least one metric entry to delete.", "warning")
            return False

        valid_ids = []
        for metric_id in metric_ids:
            try:
                valid_ids.append(int(metric_id))
            except (TypeError, ValueError):
                continue

        valid_ids = sorted(set(valid_ids))

        if not valid_ids:
            flash("Select valid metric entries to delete.", "danger")
            return False

        metrics = (
            CategoryMetric.query.filter(CategoryMetric.category == category)
            .filter(CategoryMetric.id.in_(valid_ids))
            .all()
        )

        if not metrics:
            flash("The selected metric entries could not be found.", "danger")
            return False

        deleted_count = 0
        for metric in metrics:
            db.session.delete(metric)
            deleted_count += 1

        db.session.commit()
        flash(
            f"Deleted {deleted_count} metric entr{'y' if deleted_count == 1 else 'ies'}.",
            "success",
        )
        return True

    if form_id == "delete_metric":
        metric_id_raw = request.form.get("metric_id")
        try:
            metric_id_int = int(metric_id_raw)
        except (TypeError, ValueError):
            flash("Unable to determine which metric to delete.", "danger")
            return False

        metric = CategoryMetric.query.filter_by(id=metric_id_int, category=category).first()
        if not metric:
            flash("Metric entry could not be found for deletion.", "danger")
            return False

        db.session.delete(metric)
        db.session.commit()
        flash("Metric entry deleted successfully.", "success")
        return True

    if form_id not in {"add_metric", "update_metric"}:
        flash("Unsupported metric operation.", "danger")
        return False

    try:
        fields = _extract_metric_fields(metric_config, request.form)
    except ValueError as error:
        flash(str(error), "danger")
        return False

    if form_id == "add_metric":
        metric = CategoryMetric(category=category, **fields)
        db.session.add(metric)
        db.session.commit()
        flash("Metric entry added successfully.", "success")
        return True

    metric_id = request.form.get("metric_id")
    if not metric_id:
        flash("Unable to determine which metric to update.", "danger")
        return False

    try:
        metric_id_int = int(metric_id)
    except (TypeError, ValueError):
        flash("Unable to determine which metric to update.", "danger")
        return False

    metric = CategoryMetric.query.filter_by(id=metric_id_int, category=category).first()
    if not metric:
        flash("Metric entry could not be found for update.", "danger")
        return False

    metric.metric_name = fields["metric_name"]
    metric.recorded_date = fields["recorded_date"]
    metric.value = fields["value"]
    metric.dimension = fields["dimension"]
    metric.target = fields["target"]
    metric.unit = fields["unit"]
    db.session.commit()
    flash("Metric entry updated successfully.", "success")
    return True


def _line_chart(labels: List[str], datasets: List[Dict[str, object]]) -> Dict[str, object]:
    return {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": _chart_options(stack_y=False),
    }


def _bar_chart(labels: List[str], datasets: List[Dict[str, object]]) -> Dict[str, object]:
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": _chart_options(stack_y=False),
    }


def _stacked_bar_chart(labels: List[str], datasets: List[Dict[str, object]]) -> Dict[str, object]:
    options = _chart_options(stack_y=True)
    options["scales"]["x"]["stacked"] = True
    options["scales"]["y"]["stacked"] = True
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": options,
    }


def _dataset(
    *,
    label: str,
    data: List[float],
    color: str,
    background_alpha: float | None = None,
    border_alpha: float | None = None,
    is_bar: bool = False,
) -> Dict[str, object]:
    background_alpha = background_alpha if background_alpha is not None else (0.8 if is_bar else 0.25)
    border_color = (
        _with_alpha(color, border_alpha) if border_alpha is not None and color.startswith("#") else color
    )
    background_color = _with_alpha(color, background_alpha) if color.startswith("#") else color
    return {
        "label": label,
        "data": data,
        "borderColor": border_color,
        "backgroundColor": background_color,
        "borderWidth": 2,
        "tension": 0.35,
        "fill": not is_bar,
        "hoverBorderWidth": 2,
        "borderRadius": 6 if is_bar else 0,
    }


def _chart_options(*, stack_y: bool) -> Dict[str, object]:
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "interaction": {"mode": "index", "intersect": False},
        "plugins": {
            "legend": {"display": True},
            "tooltip": {"enabled": True, "usePointStyle": True},
        },
        "scales": {
            "x": {
                "ticks": {"maxRotation": 45, "minRotation": 45},
                "grid": {"display": False},
            },
            "y": {
                "beginAtZero": True,
                "grid": {"color": "rgba(0,0,0,0.05)"},
                "stacked": stack_y,
            },
        },
    }


def _nav_links(category: str) -> Dict[str, Dict[str, str]]:
    index = CATEGORY_SEQUENCE.index(category)
    prev_category = CATEGORY_SEQUENCE[(index - 1) % len(CATEGORY_SEQUENCE)]
    next_category = CATEGORY_SEQUENCE[(index + 1) % len(CATEGORY_SEQUENCE)]
    return {
        "prev": {
            "name": prev_category,
            "url": url_for(CATEGORY_ENDPOINTS[prev_category]),
        },
        "next": {
            "name": next_category,
            "url": url_for(CATEGORY_ENDPOINTS[next_category]),
        },
    }


def _with_alpha(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError("Expected 6 character hex color")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


__all__ = [
    "safety_dashboard",
    "quality_dashboard",
    "delivery_dashboard",
    "people_dashboard",
    "materials_dashboard",
]


def register(bp):
    bp.add_url_rule("/mdi/safety", view_func=safety_dashboard, methods=["GET", "POST"])
    bp.add_url_rule("/mdi/quality", view_func=quality_dashboard, methods=["GET", "POST"])
    bp.add_url_rule("/mdi/delivery", view_func=delivery_dashboard, methods=["GET", "POST"])
    bp.add_url_rule("/mdi/people", view_func=people_dashboard, methods=["GET", "POST"])
    bp.add_url_rule("/mdi/materials", view_func=materials_dashboard)
