from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from typing import Dict, List, Any


from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import (
    ProductionCustomer,
    ProductionDailyCustomerTotal,
    ProductionDailyRecord,
    ProductionHistorySettings,
)

bp = Blueprint("production", __name__, url_prefix="/production")

DEFAULT_CUSTOMERS: List[tuple[str, str, bool]] = [
    ("AHE", "#1f77b4", False),
    ("Bella", "#ff7f0e", False),
    ("REI", "#2ca02c", False),
    ("Savaria", "#d62728", False),
    ("ELESHI", "#9467bd", False),
    ("MORNST", "#8c564b", False),
    ("MAINE", "#e377c2", False),
    ("GARPA", "#7f7f7f", False),
    ("DMEACC", "#bcbd22", False),
    ("ADMY", "#17becf", False),
    ("Other", "#3b82f6", True),
]

LINE_SERIES: List[Dict[str, str]] = [
    {"label": "Gates Produced", "key": "produced", "color": "#2563eb"},
    {"label": "Gates Packaged", "key": "packaged", "color": "#dc2626"},
    {"label": "Controllers", "key": "controllers", "color": "#16a34a"},
    {"label": "Door Locks", "key": "door_locks", "color": "#7c3aed"},
    {"label": "Operators", "key": "operators", "color": "#f97316"},
    {"label": "COPs", "key": "cops", "color": "#0ea5e9"},
]

ADDITIONAL_METRICS: List[Dict[str, str]] = [
    {"key": "controllers_4_stop", "label": "Controllers (4 Stop)"},
    {"key": "controllers_6_stop", "label": "Controllers (6 Stop)"},
    {"key": "door_locks_lh", "label": "Door Locks (LH)"},
    {"key": "door_locks_rh", "label": "Door Locks (RH)"},
    {"key": "operators_produced", "label": "Operators Produced"},
    {"key": "cops_produced", "label": "COPs Produced"},
]

OUTPUT_VARIABLE_SOURCES: List[Dict[str, str]] = [
    {"value": "produced_sum", "label": "Gates Produced"},
    {"value": "packaged_sum", "label": "Gates Packaged"},
    {"value": "combined_total", "label": "Produced + Packaged"},
    {"value": "gates_total_hours", "label": "Gates Labor Hours"},
    {"value": "gates_employees", "label": "Gates Employees"},
    {"value": "gates_hours_ot", "label": "Gates Overtime Hours"},
    {"value": "controllers_total", "label": "Controllers (Total)"},
    {"value": "door_locks_total", "label": "Door Locks (Total)"},
    {"value": "operators_total", "label": "Operators"},
    {"value": "cops_total", "label": "COPs"},
]

MAX_FORMULA_VARIABLES = 8

DECIMAL_ZERO = Decimal("0")
DECIMAL_QUANT = Decimal("0.01")


def _ensure_default_customers() -> None:
    existing_customers = ProductionCustomer.query.all()
    if not existing_customers:
        for name, color, is_other in DEFAULT_CUSTOMERS:
            db.session.add(
                ProductionCustomer(
                    name=name,
                    color=color,
                    is_active=True,
                    is_other_bucket=is_other,
                )
            )
        db.session.commit()
        existing_customers = ProductionCustomer.query.all()

    needs_commit = False
    other_customers = [c for c in existing_customers if c.is_other_bucket]
    if not other_customers:
        db.session.add(
            ProductionCustomer(
                name="Other",
                color="#3b82f6",
                is_active=True,
                is_other_bucket=True,
            )
        )
        needs_commit = True
    elif len(other_customers) > 1:
        keeper = other_customers[0]
        for extra in other_customers[1:]:
            extra.is_other_bucket = False
            needs_commit = True
        if keeper.lump_into_other:
            keeper.lump_into_other = False
            needs_commit = True

    for customer in existing_customers:
        if not customer.color:
            customer.color = "#3b82f6"
            needs_commit = True
        if customer.is_other_bucket:
            if customer.lump_into_other:
                customer.lump_into_other = False
                needs_commit = True
            if not customer.is_active:
                customer.is_active = True
                needs_commit = True

    if needs_commit:
        db.session.commit()



def _active_customers() -> List[ProductionCustomer]:
    return (
        ProductionCustomer.query.filter_by(is_active=True)
        .order_by(ProductionCustomer.is_other_bucket.asc(), ProductionCustomer.name.asc())
        .all()
    )



def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _format_decimal(value: Decimal | int | float | str | None) -> str:
    if value is None:
        decimal_value = DECIMAL_ZERO
    else:
        if isinstance(value, Decimal):
            decimal_value = value
        else:
            decimal_value = Decimal(str(value))
    return format(decimal_value.quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP), "f")


def _get_history_settings() -> ProductionHistorySettings:
    settings = ProductionHistorySettings.query.first()
    if not settings:
        settings = ProductionHistorySettings()
        db.session.add(settings)
        db.session.commit()
    return settings


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return DECIMAL_ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return DECIMAL_ZERO


def _evaluate_output_formula(
    formula: str, variables: Dict[str, Decimal]
) -> Decimal | None:
    if not formula:
        return None

    def _eval(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == DECIMAL_ZERO:
                    raise InvalidOperation("division by zero")
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
            raise ValueError("Unsupported binary operation")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Unsupported unary operation")
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"Unknown variable: {node.id}")
            return variables[node.id]
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, (int, float, str)):
                return _to_decimal(value)
            raise ValueError("Unsupported constant type")
        raise ValueError("Unsupported expression element")

    try:
        parsed = ast.parse(formula, mode="eval")
        result = _eval(parsed)
        return result
    except (SyntaxError, ValueError, InvalidOperation):
        return None


def _clean_axis_config(config: Dict[str, Any] | None) -> Dict[str, Dict[str, float | None]]:
    default = {
        "primary": {"min": None, "max": None, "step": None},
        "secondary": {"min": None, "max": None, "step": None},
    }
    if not isinstance(config, dict):
        return default

    cleaned: Dict[str, Dict[str, float | None]] = {}
    for axis_key, axis_default in default.items():
        axis_values = config.get(axis_key) or {}
        cleaned_axis: Dict[str, float | None] = {}
        for tick_key in axis_default:
            raw_value = axis_values.get(tick_key)
            if raw_value in (None, ""):
                cleaned_axis[tick_key] = None
            else:
                try:
                    cleaned_axis[tick_key] = float(raw_value)
                except (TypeError, ValueError):
                    cleaned_axis[tick_key] = None
        cleaned[axis_key] = cleaned_axis
    return cleaned


def _empty_form_values(customers: List[ProductionCustomer]) -> Dict[str, object]:
    return {
        "gates_produced": {customer.id: 0 for customer in customers},
        "gates_packaged": {customer.id: 0 for customer in customers},

        "gates_employees": 0,
        "gates_hours_ot": _format_decimal(DECIMAL_ZERO),
        "controllers_4_stop": 0,
        "controllers_6_stop": 0,
        "door_locks_lh": 0,
        "door_locks_rh": 0,
        "operators_produced": 0,
        "cops_produced": 0,
        "additional_employees": 0,
        "additional_hours_ot": _format_decimal(DECIMAL_ZERO),
        "daily_notes": "",
    }


def _form_values_from_record(
    record: ProductionDailyRecord | None,
    customers: List[ProductionCustomer],
) -> Dict[str, object]:
    values = _empty_form_values(customers)
    if not record:
        return values

    totals_by_customer = {
        total.customer_id: total for total in record.customer_totals
    }

    for customer in customers:
        totals = totals_by_customer.get(customer.id)
        if totals:
            values["gates_produced"][customer.id] = totals.gates_produced or 0
            values["gates_packaged"][customer.id] = totals.gates_packaged or 0


    values["gates_employees"] = record.gates_employees or 0
    values["gates_hours_ot"] = _format_decimal(record.gates_hours_ot)
    values["controllers_4_stop"] = record.controllers_4_stop or 0
    values["controllers_6_stop"] = record.controllers_6_stop or 0
    values["door_locks_lh"] = record.door_locks_lh or 0
    values["door_locks_rh"] = record.door_locks_rh or 0
    values["operators_produced"] = record.operators_produced or 0
    values["cops_produced"] = record.cops_produced or 0
    values["additional_employees"] = record.additional_employees or 0
    values["additional_hours_ot"] = _format_decimal(record.additional_hours_ot)
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


def _get_decimal_value(form_key: str) -> Decimal:
    raw_value = request.form.get(form_key)
    if raw_value in (None, ""):
        return DECIMAL_ZERO
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return DECIMAL_ZERO
    if value < DECIMAL_ZERO:
        return DECIMAL_ZERO
    return value.quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP)


@bp.route("/daily-entry", methods=["GET", "POST"])
def daily_entry():
    customers = _active_customers()
    today = date.today()
    selected_date = _parse_date(request.values.get("entry_date")) or today
    record = (
        ProductionDailyRecord.query.options(
            joinedload(ProductionDailyRecord.customer_totals)
        )
        .filter_by(entry_date=selected_date)
        .first()
    )


    if request.method == "POST":
        form_date = _parse_date(request.form.get("entry_date"))
        if form_date:
            selected_date = form_date
        record = (
            ProductionDailyRecord.query.options(
                joinedload(ProductionDailyRecord.customer_totals)
            )
            .filter_by(entry_date=selected_date)
            .first()
        )

        if not record:
            record = ProductionDailyRecord(entry_date=selected_date)
            db.session.add(record)

        record.day_of_week = selected_date.strftime("%A")

        existing_totals = {
            total.customer_id: total for total in record.customer_totals
        }
        for customer in customers:
            produced_value = _get_int(f"gates_produced_{customer.id}")
            packaged_value = _get_int(f"gates_packaged_{customer.id}")
            totals = existing_totals.get(customer.id)
            if not totals:
                totals = ProductionDailyCustomerTotal(customer=customer)
                record.customer_totals.append(totals)
            totals.gates_produced = produced_value
            totals.gates_packaged = packaged_value


        record.gates_employees = _get_int("gates_employees")
        record.gates_hours_ot = _get_decimal_value("gates_hours_ot")
        record.controllers_4_stop = _get_int("controllers_4_stop")
        record.controllers_6_stop = _get_int("controllers_6_stop")
        record.door_locks_lh = _get_int("door_locks_lh")
        record.door_locks_rh = _get_int("door_locks_rh")
        record.operators_produced = _get_int("operators_produced")
        record.cops_produced = _get_int("cops_produced")
        record.additional_employees = _get_int("additional_employees")
        record.additional_hours_ot = _get_decimal_value("additional_hours_ot")
        record.daily_notes = request.form.get("daily_notes") or None

        db.session.commit()
        flash(
            f"Production totals saved for {selected_date.strftime('%B %d, %Y')}.",
            "success",
        )
        return redirect(
            url_for("production.daily_entry", entry_date=selected_date.isoformat())
        )

    form_values = _form_values_from_record(record, customers)
    grouped_customers = [
        customer
        for customer in customers
        if customer.lump_into_other and not customer.is_other_bucket
    ]
    return render_template(
        "production/daily_entry.html",
        customers=customers,
        grouped_customers=grouped_customers,

        selected_date=selected_date,
        form_values=form_values,
        record_exists=record is not None,
    )


@bp.route("/history")
def history():
    customers = _active_customers()
    history_settings = _get_history_settings()
    axis_config = _clean_axis_config(history_settings.axis_config)

    today = date.today()
    start_date = _parse_date(request.args.get("start_date")) or today.replace(day=1)
    end_date = _parse_date(request.args.get("end_date")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    records = (
        ProductionDailyRecord.query.options(
            joinedload(ProductionDailyRecord.customer_totals)
        )
        .filter(

            ProductionDailyRecord.entry_date >= start_date,
            ProductionDailyRecord.entry_date <= end_date,
        )
        .order_by(ProductionDailyRecord.entry_date.asc())
        .all()
    )

    other_customer = next((c for c in customers if c.is_other_bucket), None)
    grouped_customers = [
        customer
        for customer in customers
        if customer.lump_into_other and not customer.is_other_bucket
    ]
    primary_customers = [
        customer
        for customer in customers
        if not customer.is_other_bucket and not customer.lump_into_other
    ]

    stack_customers = primary_customers[:]
    if other_customer:
        stack_customers.append(other_customer)

    table_customers = [
        customer for customer in customers if not customer.is_other_bucket
    ]
    if other_customer:
        table_customers.append(other_customer)


    table_rows = []
    chart_labels: List[str] = []
    stack_datasets: List[Dict[str, object]] = []
    cumulative_series: Dict[str, List[int]] = {
        series["key"]: [] for series in LINE_SERIES
    }

    for customer in stack_customers:
        stack_datasets.append(
            {
                "label": customer.name,
                "data": [],
                "backgroundColor": customer.color or "#3b82f6",
                "stack": "gates-produced",
                "yAxisID": "primary",
            }
        )

    running_totals = {series["key"]: 0 for series in LINE_SERIES}
    current_month: tuple[int, int] | None = None


    output_per_hour_points: List[float | None] = []

    for record in records:
        chart_labels.append(record.entry_date.strftime("%Y-%m-%d"))
        month_key = (record.entry_date.year, record.entry_date.month)
        if month_key != current_month:
            current_month = month_key
            running_totals = {series["key"]: 0 for series in LINE_SERIES}

        totals_by_customer = {
            total.customer_id: total for total in record.customer_totals
        }

        produced_sum = 0
        packaged_sum = 0
        per_customer_produced: Dict[int, int] = {}
        per_customer_packaged: Dict[int, int] = {}

        for customer in table_customers:
            totals = totals_by_customer.get(customer.id)
            produced_value = totals.gates_produced if totals else 0
            packaged_value = totals.gates_packaged if totals else 0
            per_customer_produced[customer.id] = produced_value
            per_customer_packaged[customer.id] = packaged_value
            produced_sum += produced_value
            packaged_sum += packaged_value

        for dataset, customer in zip(stack_datasets, stack_customers):
            produced_value = per_customer_produced.get(customer.id, 0)
            if customer.is_other_bucket:
                produced_value += sum(
                    per_customer_produced.get(grouped.id, 0)
                    for grouped in grouped_customers
                )
            dataset["data"].append(produced_value)


        controllers_total = (record.controllers_4_stop or 0) + (
            record.controllers_6_stop or 0
        )
        door_locks_total = (record.door_locks_lh or 0) + (record.door_locks_rh or 0)
        operators_total = record.operators_produced or 0
        cops_total = record.cops_produced or 0

        gates_total_hours_value = record.gates_total_labor_hours
        gates_total_hours_display = _format_decimal(gates_total_hours_value)
        gates_hours_ot_display = _format_decimal(record.gates_hours_ot)
        gates_combined_total = produced_sum + packaged_sum
        available_values = {
            "produced_sum": _to_decimal(produced_sum),
            "packaged_sum": _to_decimal(packaged_sum),
            "combined_total": _to_decimal(gates_combined_total),
            "gates_total_hours": _to_decimal(gates_total_hours_value),
            "gates_employees": _to_decimal(record.gates_employees or 0),
            "gates_hours_ot": _to_decimal(record.gates_hours_ot or 0),
            "controllers_total": _to_decimal(controllers_total),
            "door_locks_total": _to_decimal(door_locks_total),
            "operators_total": _to_decimal(operators_total),
            "cops_total": _to_decimal(cops_total),
        }

        variable_values: Dict[str, Decimal] = {}
        for variable in history_settings.output_variables or []:
            name = (variable.get("name") or "").strip()
            source = variable.get("source")
            if not name or source not in available_values:
                continue
            variable_values[name] = available_values[source]

        output_per_hour_result = _evaluate_output_formula(
            history_settings.output_formula, variable_values
        )
        output_per_hour_display: str | None = None
        if output_per_hour_result is not None:
            output_per_hour_result = output_per_hour_result.quantize(
                DECIMAL_QUANT, rounding=ROUND_HALF_UP
            )
            output_per_hour_display = _format_decimal(output_per_hour_result)
            output_per_hour_points.append(float(output_per_hour_result))
        else:
            output_per_hour_points.append(None)

        additional_total_hours_value = record.additional_total_labor_hours
        additional_total_hours_display = _format_decimal(
            additional_total_hours_value
        )
        additional_hours_ot_display = _format_decimal(record.additional_hours_ot)
        additional_per_hour: List[Dict[str, str]] = []
        if additional_total_hours_value and additional_total_hours_value > DECIMAL_ZERO:
            for metric in ADDITIONAL_METRICS:
                total_value = getattr(record, metric["key"]) or 0
                per_hour_value = (
                    Decimal(total_value) / additional_total_hours_value
                ).quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP)
                additional_per_hour.append(
                    {
                        "key": metric["key"],
                        "label": metric["label"],
                        "per_hour": _format_decimal(per_hour_value),
                    }
                )

        running_totals["produced"] += produced_sum
        running_totals["packaged"] += packaged_sum
        running_totals["controllers"] += controllers_total
        running_totals["door_locks"] += door_locks_total
        running_totals["operators"] += operators_total
        running_totals["cops"] += cops_total

        for series in LINE_SERIES:
            cumulative_series[series["key"]].append(running_totals[series["key"]])


        table_rows.append(
            {
                "record": record,
                "produced_sum": produced_sum,
                "packaged_sum": packaged_sum,
                "gates_combined_total": gates_combined_total,
                "per_customer_produced": per_customer_produced,
                "per_customer_packaged": per_customer_packaged,
                "controllers_total": controllers_total,
                "door_locks_total": door_locks_total,
                "operators_total": operators_total,
                "cops_total": cops_total,
                "gates_employees": record.gates_employees or 0,
                "gates_hours_ot": gates_hours_ot_display,
                "gates_total_hours": gates_total_hours_display,
                "gates_output_per_hour": output_per_hour_display,
                "additional_employees": record.additional_employees or 0,
                "additional_hours_ot": additional_hours_ot_display,
                "additional_total_hours": additional_total_hours_display,
                "additional_per_hour": additional_per_hour,
            }
        )

    line_datasets = []
    for series in LINE_SERIES:
        line_datasets.append(
            {
                "label": series["label"],
                "data": cumulative_series[series["key"]],
                "borderColor": series["color"],
                "backgroundColor": series["color"],
                "version": 0.2,
                "fill": False,
            }
        )

    overlay_dataset: Dict[str, object] | None = None
    if any(value is not None for value in output_per_hour_points):
        overlay_dataset = {
            "label": history_settings.output_label,
            "data": output_per_hour_points,
            "type": "line",
            "yAxisID": "secondary",
            "borderColor": "#0f766e",
            "backgroundColor": "#0f766e",
            "tension": 0.2,
            "fill": False,
            "spanGaps": True,
            "pointRadius": 3,
        }

    goal_dataset: Dict[str, object] | None = None
    if (
        history_settings.show_goal_line
        and history_settings.goal_line_value is not None
        and chart_labels
    ):
        goal_value = _to_decimal(history_settings.goal_line_value)
        goal_dataset = {
            "label": "Goal",
            "data": [float(goal_value) for _ in chart_labels],
            "type": "line",
            "yAxisID": "primary",
            "borderColor": "#fbbf24",
            "backgroundColor": "#fbbf24",
            "borderDash": [6, 6],
            "fill": False,
            "pointRadius": 0,
        }

    grouped_names = [customer.name for customer in grouped_customers]

    return render_template(
        "production/history.html",
        customers=table_customers,
        chart_customers=stack_customers,
        grouped_customer_names=grouped_names,

        table_rows=table_rows,
        chart_labels=chart_labels,
        stacked_datasets=stack_datasets,
        line_datasets=line_datasets,
        overlay_dataset=overlay_dataset,
        goal_dataset=goal_dataset,
        start_date=start_date,
        end_date=end_date,
        history_settings=history_settings,
        axis_config=axis_config,
    )


@bp.route("/settings", methods=["GET", "POST"])
def production_settings():
    _ensure_default_customers()
    customers = (
        ProductionCustomer.query.order_by(
            ProductionCustomer.is_other_bucket.asc(), ProductionCustomer.name.asc()
        ).all()
    )
    history_settings = _get_history_settings()
    axis_config = _clean_axis_config(history_settings.axis_config)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("new_customer_name") or "").strip()
            color = (request.form.get("new_customer_color") or "#3b82f6").strip()
            if not name:
                flash("Customer name is required.", "error")
                return redirect(url_for("production.production_settings"))

            existing = ProductionCustomer.query.filter(
                func.lower(ProductionCustomer.name) == name.lower()
            ).first()
            if existing:
                flash(f"A customer named {name} already exists.", "error")
                return redirect(url_for("production.production_settings"))

            if not (color.startswith("#") and len(color) == 7):
                color = "#3b82f6"

            new_customer = ProductionCustomer(
                name=name,
                color=color,
                is_active=True,
                is_other_bucket=False,
            )
            db.session.add(new_customer)
            db.session.commit()
            flash(f"Added customer {name}.", "success")
            return redirect(url_for("production.production_settings"))

        if action == "update":
            changes_made = False
            for customer in customers:
                color_value = (request.form.get(f"color_{customer.id}") or customer.color).strip()
                if not (color_value.startswith("#") and len(color_value) == 7):
                    color_value = customer.color or "#3b82f6"
                if color_value != customer.color:
                    customer.color = color_value
                    changes_made = True

                if customer.is_other_bucket:
                    if customer.lump_into_other:
                        customer.lump_into_other = False
                        changes_made = True
                    if not customer.is_active:
                        customer.is_active = True
                        changes_made = True
                    continue

                new_name = (request.form.get(f"name_{customer.id}") or customer.name).strip()
                if not new_name:
                    flash("Customer names cannot be blank.", "error")
                    db.session.rollback()
                    return redirect(url_for("production.production_settings"))
                if new_name != customer.name:
                    conflict = ProductionCustomer.query.filter(
                        ProductionCustomer.id != customer.id,
                        func.lower(ProductionCustomer.name) == new_name.lower(),
                    ).first()
                    if conflict:
                        flash(
                            f"A customer named {new_name} already exists.",
                            "error",
                        )
                        db.session.rollback()
                        return redirect(url_for("production.production_settings"))
                    customer.name = new_name
                    changes_made = True

                lump_value = request.form.get(f"lump_into_other_{customer.id}") is not None
                if lump_value != customer.lump_into_other:
                    customer.lump_into_other = lump_value
                    changes_made = True

                active_value = request.form.get(f"is_active_{customer.id}") is not None
                if active_value != customer.is_active:
                    customer.is_active = active_value
                    changes_made = True

            if changes_made:
                db.session.commit()
                flash("Production customer settings updated.", "success")
            else:
                flash("No changes detected.", "info")
            return redirect(url_for("production.production_settings"))

        if action == "update-history-settings":
            output_label = (request.form.get("output_label") or "").strip()
            output_formula = (request.form.get("output_formula") or "").strip()

            errors: List[str] = []
            if not output_formula:
                errors.append("Formula is required.")

            variables: List[Dict[str, str]] = []
            valid_sources = {option["value"] for option in OUTPUT_VARIABLE_SOURCES}
            for index in range(MAX_FORMULA_VARIABLES):
                variable_name = (
                    request.form.get(f"variable_name_{index}") or ""
                ).strip()
                variable_source = request.form.get(f"variable_source_{index}") or ""

                if not variable_name and not variable_source:
                    continue
                if not variable_name or not variable_source:
                    errors.append("Each variable requires both a name and a source.")
                    continue
                if variable_source not in valid_sources:
                    errors.append(
                        f"Invalid data source selected for {variable_name}."
                    )
                    continue
                variables.append({"name": variable_name, "source": variable_source})

            axis_values = {
                "primary": {"min": None, "max": None, "step": None},
                "secondary": {"min": None, "max": None, "step": None},
            }
            axis_field_map = {
                "primary_min": ("primary", "min", "Primary axis minimum"),
                "primary_max": ("primary", "max", "Primary axis maximum"),
                "primary_step": ("primary", "step", "Primary axis increment"),
                "secondary_min": ("secondary", "min", "Secondary axis minimum"),
                "secondary_max": ("secondary", "max", "Secondary axis maximum"),
                "secondary_step": ("secondary", "step", "Secondary axis increment"),
            }

            for field, (axis_key, axis_attr, label) in axis_field_map.items():
                raw_value = request.form.get(field)
                if raw_value in (None, ""):
                    axis_values[axis_key][axis_attr] = None
                    continue
                try:
                    axis_values[axis_key][axis_attr] = float(
                        Decimal(str(raw_value))
                    )
                except (InvalidOperation, ValueError):
                    errors.append(f"{label} must be a number.")
                    axis_values[axis_key][axis_attr] = None

            show_goal_line = bool(request.form.get("show_goal_line"))
            goal_line_value_raw = request.form.get("goal_line_value")
            goal_line_value: Decimal | None = history_settings.goal_line_value
            if show_goal_line:
                if goal_line_value_raw in (None, ""):
                    errors.append(
                        "Goal line value is required when the goal line is enabled."
                    )
                else:
                    try:
                        goal_line_value = Decimal(str(goal_line_value_raw)).quantize(
                            DECIMAL_QUANT, rounding=ROUND_HALF_UP
                        )
                    except (InvalidOperation, ValueError):
                        errors.append("Goal line value must be a number.")

            if not variables:
                errors.append("At least one variable must be defined for the formula.")

            if errors:
                for message in errors:
                    flash(message, "error")
            else:
                history_settings.output_label = (
                    output_label or "Output per Labor Hour"
                )
                history_settings.output_formula = output_formula
                history_settings.output_variables = variables
                history_settings.axis_config = axis_values
                history_settings.show_goal_line = (
                    show_goal_line and goal_line_value is not None
                )
                history_settings.goal_line_value = goal_line_value
                db.session.add(history_settings)
                db.session.commit()
                flash("Production history settings updated", "success")
            return redirect(url_for("production.production_settings"))

    grouped_customers = [
        customer
        for customer in customers
        if customer.lump_into_other and not customer.is_other_bucket
    ]
    return render_template(
        "production/settings.html",
        customers=customers,
        grouped_customers=grouped_customers,
        history_settings=history_settings,
        axis_config=axis_config,
        variable_sources=OUTPUT_VARIABLE_SOURCES,
        max_formula_variables=MAX_FORMULA_VARIABLES,
    )

