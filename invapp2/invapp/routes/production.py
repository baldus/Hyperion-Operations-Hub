from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from invapp.extensions import db
from invapp.models import ProductionDailyRecord

bp = Blueprint("production", __name__, url_prefix="/production")

CUSTOMERS: List[Tuple[str, str]] = [
    ("AHE", "ahe"),
    ("Bella", "bella"),
    ("REI", "rei"),
    ("Savaria", "savaria"),
    ("ELESHI", "eleshi"),
    ("MORNST", "mornst"),
    ("MAINE", "maine"),
    ("GARPA", "garpa"),
    ("DMEACC", "dmeacc"),
    ("ADMY", "admy"),
    ("Other", "other"),
]

STACK_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
    "#3b82f6",
]

LINE_SERIES = [
    ("Gates Produced", "produced", "#2563eb"),
    ("Gates Packaged", "packaged", "#dc2626"),
    ("Controllers", "controllers", "#16a34a"),
    ("Door Locks", "door_locks", "#7c3aed"),
    ("Operators", "operators", "#f97316"),
    ("COPs", "cops", "#0ea5e9"),
]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _empty_form_values() -> Dict[str, object]:
    base = {
        "gates_produced": {key: 0 for _, key in CUSTOMERS},
        "gates_packaged": {key: 0 for _, key in CUSTOMERS},
        "controllers_4_stop": 0,
        "controllers_6_stop": 0,
        "door_locks_lh": 0,
        "door_locks_rh": 0,
        "operators_produced": 0,
        "cops_produced": 0,
        "daily_notes": "",
    }
    return base


def _form_values_from_record(record: ProductionDailyRecord | None) -> Dict[str, object]:
    values = _empty_form_values()
    if not record:
        return values

    for _, key in CUSTOMERS:
        values["gates_produced"][key] = getattr(record, f"gates_produced_{key}", 0)
        values["gates_packaged"][key] = getattr(record, f"gates_packaged_{key}", 0)

    values["controllers_4_stop"] = record.controllers_4_stop or 0
    values["controllers_6_stop"] = record.controllers_6_stop or 0
    values["door_locks_lh"] = record.door_locks_lh or 0
    values["door_locks_rh"] = record.door_locks_rh or 0
    values["operators_produced"] = record.operators_produced or 0
    values["cops_produced"] = record.cops_produced or 0
    values["daily_notes"] = record.daily_notes or ""
    return values


def _get_int(form_key: str) -> int:
    raw_value = request.form.get(form_key)
    if raw_value in (None, ""):
        return 0
    try:
        return max(int(raw_value), 0)
    except ValueError:
        return 0


@bp.route("/daily-entry", methods=["GET", "POST"])
def daily_entry():
    today = date.today()
    selected_date = _parse_date(request.values.get("entry_date")) or today
    record = ProductionDailyRecord.query.filter_by(entry_date=selected_date).first()

    if request.method == "POST":
        form_date = _parse_date(request.form.get("entry_date"))
        if form_date:
            selected_date = form_date
        record = ProductionDailyRecord.query.filter_by(entry_date=selected_date).first()
        if not record:
            record = ProductionDailyRecord(entry_date=selected_date)
            db.session.add(record)

        record.day_of_week = selected_date.strftime("%A")

        for _, key in CUSTOMERS:
            setattr(record, f"gates_produced_{key}", _get_int(f"gates_produced_{key}"))
            setattr(record, f"gates_packaged_{key}", _get_int(f"gates_packaged_{key}"))

        record.controllers_4_stop = _get_int("controllers_4_stop")
        record.controllers_6_stop = _get_int("controllers_6_stop")
        record.door_locks_lh = _get_int("door_locks_lh")
        record.door_locks_rh = _get_int("door_locks_rh")
        record.operators_produced = _get_int("operators_produced")
        record.cops_produced = _get_int("cops_produced")
        record.daily_notes = request.form.get("daily_notes") or None

        db.session.commit()
        flash(
            f"Production totals saved for {selected_date.strftime('%B %d, %Y')}.",
            "success",
        )
        return redirect(
            url_for("production.daily_entry", entry_date=selected_date.isoformat())
        )

    form_values = _form_values_from_record(record)
    return render_template(
        "production/daily_entry.html",
        customers=CUSTOMERS,
        selected_date=selected_date,
        form_values=form_values,
        record_exists=record is not None,
    )


@bp.route("/history")
def history():
    today = date.today()
    start_date = _parse_date(request.args.get("start_date")) or today.replace(day=1)
    end_date = _parse_date(request.args.get("end_date")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    records = (
        ProductionDailyRecord.query.filter(
            ProductionDailyRecord.entry_date >= start_date,
            ProductionDailyRecord.entry_date <= end_date,
        )
        .order_by(ProductionDailyRecord.entry_date.asc())
        .all()
    )

    table_rows = []
    chart_labels: List[str] = []
    stack_datasets: List[Dict[str, object]] = []
    cumulative_series: Dict[str, List[int]] = {
        key: [] for _, key, _ in LINE_SERIES
    }

    for index, (label, key) in enumerate(CUSTOMERS):
        color = STACK_COLORS[index % len(STACK_COLORS)]
        stack_datasets.append({
            "label": label,
            "data": [],
            "backgroundColor": color,
            "stack": "gates-produced",
        })

    running_totals = {key: 0 for _, key, _ in LINE_SERIES}
    current_month: Tuple[int, int] | None = None

    for record in records:
        chart_labels.append(record.entry_date.strftime("%Y-%m-%d"))
        month_key = (record.entry_date.year, record.entry_date.month)
        if month_key != current_month:
            current_month = month_key
            running_totals = {key: 0 for _, key, _ in LINE_SERIES}

        produced_sum = 0
        packaged_sum = 0
        per_customer_produced = {}
        per_customer_packaged = {}

        for dataset, (label, key) in zip(stack_datasets, CUSTOMERS):
            produced_value = getattr(record, f"gates_produced_{key}") or 0
            packaged_value = getattr(record, f"gates_packaged_{key}") or 0
            dataset["data"].append(produced_value)
            produced_sum += produced_value
            packaged_sum += packaged_value
            per_customer_produced[label] = produced_value
            per_customer_packaged[label] = packaged_value

        controllers_total = (record.controllers_4_stop or 0) + (
            record.controllers_6_stop or 0
        )
        door_locks_total = (record.door_locks_lh or 0) + (record.door_locks_rh or 0)
        operators_total = record.operators_produced or 0
        cops_total = record.cops_produced or 0

        running_totals["produced"] += produced_sum
        running_totals["packaged"] += packaged_sum
        running_totals["controllers"] += controllers_total
        running_totals["door_locks"] += door_locks_total
        running_totals["operators"] += operators_total
        running_totals["cops"] += cops_total

        for _, key, _ in LINE_SERIES:
            cumulative_series[key].append(running_totals[key])

        table_rows.append(
            {
                "record": record,
                "produced_sum": produced_sum,
                "packaged_sum": packaged_sum,
                "per_customer_produced": per_customer_produced,
                "per_customer_packaged": per_customer_packaged,
                "controllers_total": controllers_total,
                "door_locks_total": door_locks_total,
                "operators_total": operators_total,
                "cops_total": cops_total,
            }
        )

    line_datasets = []
    for (label, key, color) in LINE_SERIES:
        line_datasets.append(
            {
                "label": label,
                "data": cumulative_series[key],
                "borderColor": color,
                "backgroundColor": color,
                "tension": 0.2,
                "fill": False,
            }
        )

    return render_template(
        "production/history.html",
        customers=CUSTOMERS,
        table_rows=table_rows,
        chart_labels=chart_labels,
        stacked_datasets=stack_datasets,
        line_datasets=line_datasets,
        start_date=start_date,
        end_date=end_date,
    )
