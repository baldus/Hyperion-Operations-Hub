from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from typing import Any, Dict, List


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
    ProductionOutputFormula,
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

DEFAULT_OUTPUT_FORMULA = "combined_output / total_hours"
DEFAULT_OUTPUT_VARIABLES = [
    {
        "name": "combined_output",
        "label": "Combined Output",
        "expression": "produced + packaged",
    },
    {
        "name": "total_hours",
        "label": "Labor Hours",
        "expression": "total_hours",
    },
]

FORMULA_METRIC_HINTS: List[Dict[str, str]] = [
    {
        "key": "produced",
        "label": "Gates Produced",
        "description": "Total gates produced for the day.",
    },
    {
        "key": "packaged",
        "label": "Gates Packaged",
        "description": "Total gates packaged for the day.",
    },
    {
        "key": "combined",
        "label": "Combined Gates Output",
        "description": "Sum of produced and packaged gates.",
    },
    {
        "key": "employees",
        "label": "Gate Employees",
        "description": "Employees assigned to gates production.",
    },
    {
        "key": "shift_hours",
        "label": "Shift Hours",
        "description": "Base shift hours per employee (8).",
    },
    {
        "key": "overtime",
        "label": "Overtime Hours",
        "description": "Overtime hours recorded for gates production.",
    },
    {
        "key": "total_hours",
        "label": "Total Labor Hours",
        "description": "Employees * shift hours plus overtime.",
    },
    {
        "key": "controllers",
        "label": "Controllers Produced",
        "description": "Total controllers (4 & 6 stop).",
    },
    {
        "key": "door_locks",
        "label": "Door Locks Produced",
        "description": "Total door locks (LH & RH).",
    },
    {
        "key": "operators",
        "label": "Operators Produced",
        "description": "Total operators completed.",
    },
    {
        "key": "cops",
        "label": "COPs Produced",
        "description": "Total COP units completed.",
    },
]

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



class FormulaEvaluationError(Exception):
    """Raised when a user-defined production formula cannot be evaluated."""


def _ensure_output_formula() -> ProductionOutputFormula:
    setting = ProductionOutputFormula.query.first()
    if setting is None:
        setting = ProductionOutputFormula(
            formula=DEFAULT_OUTPUT_FORMULA,
            variables=[dict(variable) for variable in DEFAULT_OUTPUT_VARIABLES],
        )
        db.session.add(setting)
        db.session.commit()
    return setting


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return DECIMAL_ZERO
    return Decimal(str(value))


def _default_formula_context() -> Dict[str, Decimal]:
    context = {
        "produced": Decimal("12"),
        "packaged": Decimal("6"),
        "combined": Decimal("18"),
        "employees": Decimal("3"),
        "shift_hours": ProductionDailyRecord.LABOR_SHIFT_HOURS,
        "overtime": Decimal("2"),
        "total_hours": Decimal("26"),
        "controllers": Decimal("8"),
        "door_locks": Decimal("5"),
        "operators": Decimal("4"),
        "cops": Decimal("3"),
    }
    for metric in ADDITIONAL_METRICS:
        context.setdefault(metric["key"], Decimal("1"))
    return context


def _build_formula_context(
    record: ProductionDailyRecord,
    produced_sum: int,
    packaged_sum: int,
    controllers_total: int,
    door_locks_total: int,
    operators_total: int,
    cops_total: int,
) -> Dict[str, Decimal]:
    combined_total = produced_sum + packaged_sum
    context: Dict[str, Decimal] = {
        "produced": Decimal(produced_sum),
        "packaged": Decimal(packaged_sum),
        "combined": Decimal(combined_total),
        "employees": Decimal(record.gates_employees or 0),
        "shift_hours": ProductionDailyRecord.LABOR_SHIFT_HOURS,
        "overtime": _to_decimal(record.gates_hours_ot),
        "total_hours": _to_decimal(record.gates_total_labor_hours),
        "controllers": Decimal(controllers_total),
        "door_locks": Decimal(door_locks_total),
        "operators": Decimal(operators_total),
        "cops": Decimal(cops_total),
    }
    for metric in ADDITIONAL_METRICS:
        context[metric["key"]] = Decimal(getattr(record, metric["key"]) or 0)
    return context


def _evaluate_decimal_expression(
    expression: str, context: Dict[str, Decimal]
) -> Decimal:
    if not expression:
        raise FormulaEvaluationError("Expression cannot be blank.")
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - defensive
        raise FormulaEvaluationError("Invalid expression syntax.") from exc

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
                    raise FormulaEvaluationError("Division by zero.")
                return left / right
            raise FormulaEvaluationError("Unsupported operator.")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise FormulaEvaluationError("Unsupported unary operator.")
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise FormulaEvaluationError(f"Unknown variable '{node.id}'.")
            return _to_decimal(context[node.id])
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str)):
                return Decimal(str(node.value))
            raise FormulaEvaluationError("Unsupported constant type.")
        if isinstance(node, ast.Num):  # pragma: no cover - legacy Python
            return Decimal(str(node.n))
        paren_expr = getattr(ast, "ParenExpr", None)
        if paren_expr is not None and isinstance(node, paren_expr):  # pragma: no cover - Python 3.12+
            return _eval(node.expression)
        raise FormulaEvaluationError("Unsupported expression component.")

    result = _eval(parsed.body if isinstance(parsed, ast.Expression) else parsed)
    return result


def _compute_output_values(
    formula_config: Dict[str, Any], context: Dict[str, Decimal]
) -> tuple[Decimal, List[Dict[str, Any]]]:
    working_context = dict(context)
    variables: List[Dict[str, Any]] = []
    for variable in formula_config.get("variables") or []:
        name = (variable.get("name") or "").strip()
        expression = (variable.get("expression") or "").strip()
        if not name or not expression:
            continue
        label = (variable.get("label") or name).strip() or name
        value = _evaluate_decimal_expression(expression, working_context)
        working_context[name] = value
        variables.append({"name": name, "label": label, "value": value})

    formula_text = (formula_config.get("formula") or "").strip()
    if not formula_text:
        raise FormulaEvaluationError("Formula is required.")
    result = _evaluate_decimal_expression(formula_text, working_context)
    return result, variables


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


def _process_output_formula_form(
    setting: ProductionOutputFormula,
) -> tuple[bool, Dict[str, Any]]:
    formula_text = (request.form.get("output_formula") or "").strip()
    names = [value.strip() for value in request.form.getlist("variable_name")]
    labels = [value.strip() for value in request.form.getlist("variable_label")]
    expressions = [value.strip() for value in request.form.getlist("variable_expression")]

    variables: List[Dict[str, str]] = []
    seen_names: set[str] = set()
    has_errors = False

    for name, label, expression in zip(names, labels, expressions):
        if not name and not expression and not label:
            continue
        if not name:
            flash("Variable name is required for each row.", "error")
            has_errors = True
            continue
        if name in seen_names:
            flash(f"Duplicate variable name '{name}'.", "error")
            has_errors = True
            continue
        if not expression:
            flash(f"Expression is required for variable '{name}'.", "error")
            has_errors = True
            continue
        seen_names.add(name)
        variables.append(
            {
                "name": name,
                "label": label or name,
                "expression": expression,
            }
        )

    if not formula_text:
        flash("Output formula is required.", "error")
        has_errors = True

    if has_errors:
        return False, {"formula": formula_text, "variables": variables}

    try:
        _compute_output_values(
            {"formula": formula_text, "variables": variables},
            _default_formula_context(),
        )
    except FormulaEvaluationError as exc:
        flash(f"Unable to evaluate formula: {exc}", "error")
        return False, {"formula": formula_text, "variables": variables}

    setting.formula = formula_text
    setting.variables = variables
    db.session.commit()
    return True, {"formula": setting.formula, "variables": variables}


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
            }
        )

    running_totals = {series["key"]: 0 for series in LINE_SERIES}
    current_month: tuple[int, int] | None = None
    formula_setting = _ensure_output_formula()
    formula_config = {
        "formula": formula_setting.formula,
        "variables": formula_setting.variables or [],
    }


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
        gates_output_per_hour_display: str | None = None
        output_variables_display: List[Dict[str, str]] = []
        try:
            output_value, variable_values = _compute_output_values(
                formula_config,
                _build_formula_context(
                    record,
                    produced_sum,
                    packaged_sum,
                    controllers_total,
                    door_locks_total,
                    operators_total,
                    cops_total,
                ),
            )
        except FormulaEvaluationError:
            output_value = None
            variable_values = []
        else:
            gates_output_per_hour_display = _format_decimal(output_value)
            output_variables_display = [
                {
                    "name": variable["name"],
                    "label": variable["label"],
                    "value": _format_decimal(variable["value"]),
                }
                for variable in variable_values
            ]

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
                "gates_output_per_hour": gates_output_per_hour_display,
                "output_variables": output_variables_display,
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
        start_date=start_date,
        end_date=end_date,
    )


@bp.route("/settings", methods=["GET", "POST"])
def production_settings():
    _ensure_default_customers()
    customers = (
        ProductionCustomer.query.order_by(
            ProductionCustomer.is_other_bucket.asc(), ProductionCustomer.name.asc()
        ).all()
    )
    formula_setting = _ensure_output_formula()
    formula_form_values = {
        "formula": formula_setting.formula,
        "variables": formula_setting.variables or [],
    }

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

        elif action == "update":
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

        elif action == "update_formula":
            success, formula_form_values = _process_output_formula_form(formula_setting)
            if success:
                flash("Output per labor hour formula updated.", "success")
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
        output_formula_form=formula_form_values,
        formula_metric_hints=FORMULA_METRIC_HINTS,
    )

