from __future__ import annotations

import ast
import csv
import io
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from itertools import zip_longest
from typing import Any, Dict, List


from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.auth import blueprint_page_guard
from invapp.models import (
    ProductionChartSettings,
    ProductionCustomer,
    ProductionDailyCustomerTotal,
    ProductionDailyGateCompletion,
    ProductionDailyRecord,
    ProductionOutputFormula,
)

bp = Blueprint("production", __name__, url_prefix="/production")

bp.before_request(blueprint_page_guard("production"))

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
    {"label": "Gates Packaged", "key": "packaged", "color": "#2563eb"},
    {"label": "Controllers", "key": "controllers", "color": "#16a34a"},
    {"label": "Door Locks", "key": "door_locks", "color": "#7c3aed"},
    {"label": "Operators", "key": "operators", "color": "#f97316"},
    {"label": "COPs", "key": "cops", "color": "#0ea5e9"},
]


def _parse_optional_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None

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
        "label": "Gates Produced (legacy)",
        "description": "Historical gates produced total (read-only).",
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


def _format_optional_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP), "f")


def _empty_form_values(customers: List[ProductionCustomer]) -> Dict[str, object]:
    return {
        "gates_packaged": {customer.id: "" for customer in customers},

        "gates_employees": "",
        "gates_hours_ot": "",
        "controllers_4_stop": "",
        "controllers_6_stop": "",
        "door_locks_lh": "",
        "door_locks_rh": "",
        "operators_produced": "",
        "cops_produced": "",
        "additional_employees": "",
        "additional_hours_ot": "",
        "daily_notes": "",
        "gate_completions": [],
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
    values["gate_completions"] = [
        {
            "id": completion.id,
            "order_number": completion.order_number or "",
            "customer_name": completion.customer_name or "",
            "gates_completed": completion.gates_completed or 0,
            "po_number": completion.po_number or "",
            "marked_for_delete": False,
        }
        for completion in record.gate_completions
    ]
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


def _parse_non_negative_int(value: str | None) -> int:
    if value in (None, ""):
        return 0
    try:
        return max(int(value), 0)
    except ValueError:
        return 0


def _completion_rows_from_request() -> tuple[list[dict[str, Any]], set[int]]:
    submitted_ids = request.form.getlist("completion_id")
    submitted_order_numbers = request.form.getlist("completion_order_number")
    submitted_customers = request.form.getlist("completion_customer")
    submitted_gate_counts = request.form.getlist("completion_gate_count")
    submitted_po_numbers = request.form.getlist("completion_po_number")
    delete_ids = {
        int(value)
        for value in request.form.getlist("completion_delete_ids")
        if value and value.isdigit()
    }

    rows: list[dict[str, Any]] = []
    for (
        completion_id_raw,
        order_number,
        customer_name,
        gate_count_raw,
        po_number,
    ) in zip_longest(
        submitted_ids,
        submitted_order_numbers,
        submitted_customers,
        submitted_gate_counts,
        submitted_po_numbers,
        fillvalue="",
    ):
        order_number = (order_number or "").strip()
        customer_name = (customer_name or "").strip()
        po_number = (po_number or "").strip()
        gate_count_text = (gate_count_raw or "").strip()
        gates_completed = _parse_non_negative_int(gate_count_text)

        completion_id = None
        if completion_id_raw and completion_id_raw.isdigit():
            completion_id = int(completion_id_raw)

        is_empty = (
            not order_number
            and not customer_name
            and gates_completed == 0
            and not po_number
        )

        if completion_id is None and is_empty:
            continue

        marked_for_delete = (
            completion_id is not None and completion_id in delete_ids
        )

        rows.append(
            {
                "id": completion_id,
                "order_number": order_number,
                "customer_name": customer_name,
                "gates_completed": gates_completed,
                "gates_completed_raw": gate_count_text,
                "po_number": po_number,
                "is_empty": is_empty,
                "marked_for_delete": marked_for_delete,
            }
        )

    return rows, delete_ids


def _form_values_from_post(
    customers: List[ProductionCustomer],
    completion_rows: list[dict[str, Any]],
) -> Dict[str, object]:
    values = _empty_form_values(customers)

    for customer in customers:
        values["gates_packaged"][customer.id] = (
            request.form.get(f"gates_packaged_{customer.id}") or ""
        ).strip()

    values["gates_employees"] = (request.form.get("gates_employees") or "").strip()
    values["gates_hours_ot"] = (
        request.form.get("gates_hours_ot") or ""
    ).strip()
    values["controllers_4_stop"] = (
        request.form.get("controllers_4_stop") or ""
    ).strip()
    values["controllers_6_stop"] = (
        request.form.get("controllers_6_stop") or ""
    ).strip()
    values["door_locks_lh"] = (request.form.get("door_locks_lh") or "").strip()
    values["door_locks_rh"] = (request.form.get("door_locks_rh") or "").strip()
    values["operators_produced"] = (
        request.form.get("operators_produced") or ""
    ).strip()
    values["cops_produced"] = (request.form.get("cops_produced") or "").strip()
    values["additional_employees"] = (
        request.form.get("additional_employees") or ""
    ).strip()
    values["additional_hours_ot"] = (
        request.form.get("additional_hours_ot") or ""
    ).strip()
    values["daily_notes"] = request.form.get("daily_notes") or ""

    completion_values: list[dict[str, Any]] = []
    for row in completion_rows:
        completion_values.append(
            {
                "id": row["id"],
                "order_number": row["order_number"],
                "customer_name": row["customer_name"],
                "gates_completed": (
                    row["gates_completed_raw"]
                    if row["gates_completed_raw"] != ""
                    else (str(row["gates_completed"]) if row["gates_completed"] else "")
                ),
                "po_number": row["po_number"],
                "marked_for_delete": row["marked_for_delete"],
            }
        )
    values["gate_completions"] = completion_values
    return values


def _aggregate_packaged_totals(
    customers: List[ProductionCustomer],
    completion_rows: list[dict[str, Any]],
) -> tuple[dict[int, int], set[str], set[str]]:
    totals: dict[int, int] = {customer.id: 0 for customer in customers}
    redirected_to_other: set[str] = set()
    unmatched_customers: set[str] = set()
    other_customer = next((c for c in customers if c.is_other_bucket), None)
    lookup = {customer.name.casefold(): customer for customer in customers}

    for row in completion_rows:
        if row["marked_for_delete"] or row["is_empty"]:
            continue
        gates_completed = row["gates_completed"]
        if gates_completed <= 0:
            continue
        customer_name = row["customer_name"]
        if not customer_name:
            continue
        match = lookup.get(customer_name.casefold())
        if match is not None:
            totals[match.id] += gates_completed
            continue
        if other_customer is not None:
            totals[other_customer.id] += gates_completed
            redirected_to_other.add(customer_name)
        else:
            unmatched_customers.add(customer_name)

    return totals, redirected_to_other, unmatched_customers


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


@bp.route("/final-process-entry", methods=["GET", "POST"])
def final_process_entry():
    today = date.today()
    selected_date = _parse_date(request.values.get("entry_date")) or today
    customers = _active_customers()
    customer_lookup = {str(customer.id): customer for customer in customers}
    manual_selection = "__manual__"

    form_data = {
        "entry_date": selected_date,
        "order_number": "",
        "customer_id": "",
        "customer_manual": "",
        "gates_completed": "",
        "po_number": "",
    }

    if request.method == "POST":
        selected_date = _parse_date(request.form.get("entry_date")) or today
        form_data["entry_date"] = selected_date
        order_number = (request.form.get("order_number") or "").strip()
        customer_choice = (request.form.get("customer_id") or "").strip()
        customer_manual = (request.form.get("customer_manual") or "").strip()
        po_number = (request.form.get("po_number") or "").strip()
        gates_completed_raw = request.form.get("gates_completed")
        gates_completed = _parse_non_negative_int(gates_completed_raw)

        selected_customer_name: str | None = None
        if customer_choice in customer_lookup:
            selected_customer_name = customer_lookup[customer_choice].name
            form_data["customer_id"] = customer_choice
            form_data["customer_manual"] = ""
        elif customer_manual:
            selected_customer_name = customer_manual
            form_data["customer_id"] = manual_selection
            form_data["customer_manual"] = customer_manual
        elif customer_choice == manual_selection:
            form_data["customer_id"] = manual_selection
            form_data["customer_manual"] = ""
        else:
            form_data["customer_id"] = ""
            form_data["customer_manual"] = ""

        form_data.update(
            {
                "order_number": order_number,
                "gates_completed": gates_completed_raw or "",
                "po_number": po_number,
            }
        )

        has_errors = False
        if not order_number:
            flash("Order number is required to record a completion.", "error")
            has_errors = True
        if gates_completed <= 0:
            flash("Number of gates completed must be greater than zero.", "error")
            has_errors = True

        if not has_errors:
            record = ProductionDailyRecord.query.filter_by(
                entry_date=selected_date
            ).first()
            if not record:
                record = ProductionDailyRecord(
                    entry_date=selected_date,
                    day_of_week=selected_date.strftime("%A"),
                )
                db.session.add(record)
            else:
                record.day_of_week = selected_date.strftime("%A")

            record.gate_completions.append(
                ProductionDailyGateCompletion(
                    order_number=order_number,
                    customer_name=selected_customer_name or None,
                    gates_completed=gates_completed,
                    po_number=po_number or None,
                )
            )
            db.session.commit()
            flash(
                f"Recorded completion for order {order_number} on {selected_date.strftime('%B %d, %Y')}.",
                "success",
            )
            return redirect(
                url_for(
                    "production.final_process_entry",
                    entry_date=selected_date.isoformat(),
                )
            )

    record = (
        ProductionDailyRecord.query.options(
            joinedload(ProductionDailyRecord.gate_completions)
        )
        .filter_by(entry_date=selected_date)
        .first()
    )
    completions = record.gate_completions if record else []

    return render_template(
        "production/final_process_entry.html",
        selected_date=selected_date,
        form_data=form_data,
        completions=completions,
        customers=customers,
        manual_selection=manual_selection,
    )


@bp.route("/daily-entry", methods=["GET", "POST"])
def daily_entry():
    customers = _active_customers()
    grouped_customers = [
        customer
        for customer in customers
        if customer.lump_into_other and not customer.is_other_bucket
    ]
    today = date.today()
    selected_date = _parse_date(request.values.get("entry_date")) or today
    record = (
        ProductionDailyRecord.query.options(
            joinedload(ProductionDailyRecord.customer_totals)
        )
        .filter_by(entry_date=selected_date)
        .first()
    )
    record_exists = record is not None

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
        record_exists = record is not None

        completion_rows, delete_ids = _completion_rows_from_request()

        if request.form.get("fill_packaged_from_completions"):
            form_values = _form_values_from_post(customers, completion_rows)
            totals, redirected_names, unmatched_names = _aggregate_packaged_totals(
                customers, completion_rows
            )
            total_applied = sum(totals.values())
            if total_applied > 0:
                for customer in customers:
                    form_values["gates_packaged"][customer.id] = str(
                        totals.get(customer.id, 0)
                    )
                flash(
                    "Gates packaged totals were populated from finished gate entries.",
                    "success",
                )
            else:
                flash(
                    "No finished gate entries with customer names and positive gate counts were found to apply.",
                    "info",
                )

            if redirected_names:
                redirected_list = ", ".join(sorted(redirected_names))
                flash(
                    f"The following customers were counted under Other: {redirected_list}.",
                    "info",
                )

            if unmatched_names:
                unmatched_list = ", ".join(sorted(unmatched_names))
                flash(
                    "The following customers could not be matched to an active customer and were skipped: "
                    f"{unmatched_list}.",
                    "warning",
                )

            return render_template(
                "production/daily_entry.html",
                customers=customers,
                grouped_customers=grouped_customers,
                selected_date=selected_date,
                form_values=form_values,
                record_exists=record_exists,
                extra_completion_rows=3,
            )

        if not record:
            record = ProductionDailyRecord(entry_date=selected_date)
            db.session.add(record)

        record.day_of_week = selected_date.strftime("%A")

        existing_totals = {
            total.customer_id: total for total in record.customer_totals
        }
        for customer in customers:
            produced_field = f"gates_produced_{customer.id}"
            packaged_value = _get_int(f"gates_packaged_{customer.id}")
            totals = existing_totals.get(customer.id)
            if not totals:
                totals = ProductionDailyCustomerTotal(customer=customer)
                record.customer_totals.append(totals)
            if produced_field in request.form:
                totals.gates_produced = _get_int(produced_field)
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

        existing_completions = {
            completion.id: completion for completion in record.gate_completions
        }

        processed_ids: set[int] = set()
        for row in completion_rows:
            completion_id = row["id"]
            if completion_id is not None:
                processed_ids.add(completion_id)
                completion = existing_completions.get(completion_id)
                if not completion:
                    continue
                if row["marked_for_delete"] or row["is_empty"]:
                    db.session.delete(completion)
                    continue
                completion.order_number = row["order_number"] or completion.order_number
                completion.customer_name = row["customer_name"] or None
                completion.gates_completed = row["gates_completed"]
                completion.po_number = row["po_number"] or None
                continue

            if row["is_empty"] or row["marked_for_delete"]:
                continue

            record.gate_completions.append(
                ProductionDailyGateCompletion(
                    order_number=row["order_number"],
                    customer_name=row["customer_name"] or None,
                    gates_completed=row["gates_completed"],
                    po_number=row["po_number"] or None,
                )
            )

        for completion_id, completion in list(existing_completions.items()):
            if completion_id not in processed_ids and completion_id not in delete_ids:
                db.session.delete(completion)

        db.session.commit()
        flash(
            f"Production totals saved for {selected_date.strftime('%B %d, %Y')}.",
            "success",
        )
        return redirect(
            url_for("production.daily_entry", entry_date=selected_date.isoformat())
        )

    form_values = _form_values_from_record(record, customers)
    return render_template(
        "production/daily_entry.html",
        customers=customers,
        grouped_customers=grouped_customers,
        selected_date=selected_date,
        form_values=form_values,
        record_exists=record is not None,
        extra_completion_rows=3,
    )


def _build_history_context(start_date: date, end_date: date) -> Dict[str, Any]:
    customers = _active_customers()

    chart_settings = ProductionChartSettings.get_or_create()

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
    chart_entry_dates: List[date] = []
    packaged_stack_datasets: List[Dict[str, object]] = []
    overlay_values: List[float | None] = []
    total_packaged_values: List[int] = []
    cumulative_series: Dict[str, List[int]] = {
        series["key"]: [] for series in LINE_SERIES
    }

    for customer in stack_customers:
        packaged_stack_datasets.append(
            {
                "label": customer.name,
                "data": [],
                "backgroundColor": customer.color or "#3b82f6",
                "stack": "gates-packaged",
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
        chart_entry_dates.append(record.entry_date)
        month_key = (record.entry_date.year, record.entry_date.month)
        if month_key != current_month:
            current_month = month_key
            running_totals = {series["key"]: 0 for series in LINE_SERIES}

        totals_by_customer = {
            total.customer_id: total for total in record.customer_totals
        }

        produced_sum = 0
        packaged_sum = 0
        per_customer_packaged: Dict[int, int] = {}

        for customer in table_customers:
            totals = totals_by_customer.get(customer.id)
            produced_value = totals.gates_produced if totals else 0
            packaged_value = totals.gates_packaged if totals else 0
            per_customer_packaged[customer.id] = packaged_value
            produced_sum += produced_value
            packaged_sum += packaged_value

        for dataset, customer in zip(packaged_stack_datasets, stack_customers):
            packaged_value = per_customer_packaged.get(customer.id, 0)
            if customer.is_other_bucket:
                packaged_value += sum(
                    per_customer_packaged.get(grouped.id, 0)
                    for grouped in grouped_customers
                )
            dataset["data"].append(packaged_value)


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

        overlay_values.append(float(output_value) if output_value is not None else None)
        total_packaged_values.append(packaged_sum)


        additional_total_hours_value = record.additional_total_labor_hours
        additional_total_hours_display = _format_decimal(
            additional_total_hours_value
        )
        additional_hours_ot_display = _format_decimal(record.additional_hours_ot)
        additional_per_hour: List[Dict[str, str]] = []
        additional_output_total_value = DECIMAL_ZERO
        if additional_total_hours_value and additional_total_hours_value > DECIMAL_ZERO:
            for metric in ADDITIONAL_METRICS:
                total_value = getattr(record, metric["key"]) or 0
                per_hour_value = (
                    Decimal(total_value) / additional_total_hours_value
                ).quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP)
                additional_output_total_value += per_hour_value
                additional_per_hour.append(
                    {
                        "key": metric["key"],
                        "label": metric["label"],
                        "per_hour": _format_decimal(per_hour_value),
                    }
                )

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
                "packaged_sum": packaged_sum,
                "gates_combined_total": gates_combined_total,
                "per_customer_packaged": per_customer_packaged,
                "controllers_total": controllers_total,
                "door_locks_total": door_locks_total,
                "operators_total": operators_total,
                "cops_total": cops_total,
                "gates_employees": record.gates_employees or 0,
                "gates_hours_ot": gates_hours_ot_display,
                "gates_total_hours": gates_total_hours_display,
                "gates_total_hours_value": float(gates_total_hours_value)
                if gates_total_hours_value is not None
                else 0.0,
                "gates_output_per_hour": gates_output_per_hour_display,
                "output_per_hour_value": float(output_value)
                if output_value is not None
                else None,
                "output_variables": output_variables_display,
                "additional_employees": record.additional_employees or 0,
                "additional_hours_ot": additional_hours_ot_display,
                "additional_total_hours": additional_total_hours_display,
                "additional_total_hours_value": float(additional_total_hours_value)
                if additional_total_hours_value is not None
                else 0.0,
                "additional_output_total_value": float(additional_output_total_value)
                if additional_per_hour
                else 0.0,
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

    trendline_values: List[float] = []
    weekday_points = [
        (index, total_packaged_values[index])
        for index, entry_date in enumerate(chart_entry_dates)
        if entry_date.weekday() < 5
    ]
    if len(weekday_points) >= 2:
        x_values = [point[0] for point in weekday_points]
        y_values = [point[1] for point in weekday_points]
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xx = sum(x * x for x in x_values)
        sum_xy = sum(x * y for x, y in zip(x_values, y_values))
        count = len(weekday_points)
        denominator = (count * sum_xx) - (sum_x ** 2)
        if denominator != 0:
            slope = ((count * sum_xy) - (sum_x * sum_y)) / denominator
            intercept = (sum_y - (slope * sum_x)) / count
            trendline_values = [slope * x + intercept for x in range(len(total_produced_values))]

    overlay_datasets: List[Dict[str, object]] = []
    if trendline_values:
        overlay_datasets.append(
            {
                "label": "Gates Packaged Trend",
                "data": trendline_values,
                "type": "line",
                "yAxisID": "y",
                "borderColor": "#6366f1",
                "backgroundColor": "#6366f1",
                "tension": 0.2,
                "fill": False,
                "pointRadius": 0,
                "borderWidth": 2,
                "order": 1,
                "stack": "gates-trend-line",
            }
        )
    if any(value is not None for value in overlay_values):
        overlay_datasets.append(
            {
                "label": "Output per Labor Hour",
                "data": overlay_values,
                "type": "line",
                "yAxisID": "y-output",
                "borderColor": "#22c55e",
                "backgroundColor": "rgba(34, 197, 94, 0.3)",
                "tension": 0.3,
                "fill": False,
                "pointRadius": 3,
                "spanGaps": True,
                "order": 2,
            }
        )

    goal_value = (
        float(chart_settings.goal_value)
        if chart_settings.goal_value is not None
        else None
    )
    if chart_labels and chart_settings.show_goal and goal_value is not None:
        overlay_datasets.append(
            {
                "label": "Gates Goal",
                "data": [goal_value for _ in chart_labels],
                "type": "line",
                "yAxisID": "y",
                "borderColor": "#f97316",
                "borderDash": [6, 6],
                "pointRadius": 0,
                "fill": False,
                "order": 3,
                "stack": "gates-goal-line",
            }
        )

    chart_axis_settings = {
        "primary": {
            "min": float(chart_settings.primary_min)
            if chart_settings.primary_min is not None
            else None,
            "max": float(chart_settings.primary_max)
            if chart_settings.primary_max is not None
            else None,
            "step": float(chart_settings.primary_step)
            if chart_settings.primary_step is not None
            else None,
        },
        "secondary": {
            "min": float(chart_settings.secondary_min)
            if chart_settings.secondary_min is not None
            else None,
            "max": float(chart_settings.secondary_max)
            if chart_settings.secondary_max is not None
            else None,
            "step": float(chart_settings.secondary_step)
            if chart_settings.secondary_step is not None
            else None,
        },
    }

    preview_record = None
    if records:
        preview_record = next(
            (record for record in records if record.entry_date == end_date),
            records[-1],
        )

    email_preview = None
    if preview_record:
        preview_row = next(
            (row for row in table_rows if row["record"].id == preview_record.id),
            None,
        )

        preview_date = preview_record.entry_date
        day_of_week = preview_record.day_of_week or preview_date.strftime("%A")
        date_display = preview_date.strftime("%B %d, %Y")
        range_start_display = start_date.strftime("%B %d, %Y")
        range_end_display = end_date.strftime("%B %d, %Y")
        range_display = f"{range_start_display} – {range_end_display}"

        summary_text = None
        if preview_row:
            packaged_total = preview_row.get("packaged_sum", 0)
            combined_total = preview_row.get("gates_combined_total", 0)

            def _format_count(value: int | None) -> str:
                return f"{int(value or 0):,}"

            packaged_breakdown: list[str] = []
            per_customer_packaged = preview_row.get("per_customer_packaged", {})

            for customer in table_customers:
                customer_id = customer.id
                packaged_value = per_customer_packaged.get(customer_id, 0)
                if packaged_value:
                    packaged_breakdown.append(
                        f"    • {customer.name}: {_format_count(packaged_value)}"
                    )

            output_per_hour = preview_row.get("gates_output_per_hour")
            output_variables = preview_row.get("output_variables", [])

            additional_per_hour = preview_row.get("additional_per_hour", [])

            gates_employees = preview_row.get("gates_employees", 0)
            gates_hours_ot = preview_row.get("gates_hours_ot", "0")
            gates_total_hours = preview_row.get("gates_total_hours", "0")

            additional_employees = preview_row.get("additional_employees", 0)
            additional_hours_ot = preview_row.get("additional_hours_ot", "0")
            additional_total_hours = preview_row.get("additional_total_hours", "0")

            summary_lines: list[str] = [
                f"Production Summary: {day_of_week}, {date_display}",
                f"Reporting Range: {range_display}",
                "",
                "Daily Production",
            ]

            summary_lines.append(
                f"- Gates Packaged: {_format_count(packaged_total)}"
            )
            if packaged_breakdown:
                summary_lines.append("  Customer Breakdown (Packaged):")
                summary_lines.extend(packaged_breakdown)

            summary_lines.append(
                f"- Combined Output: {_format_count(combined_total)}"
            )
            summary_lines.append(
                "- Output per Labor Hour: "
                + (output_per_hour if output_per_hour else "Not calculated")
            )

            summary_lines.append(
                "- Controllers: "
                f"{_format_count(preview_record.total_controllers)} "
                f"(4 Stop: {_format_count(preview_record.controllers_4_stop)}, "
                f"6 Stop: {_format_count(preview_record.controllers_6_stop)})"
            )
            summary_lines.append(
                "- Door Locks: "
                f"{_format_count(preview_record.total_door_locks)} "
                f"(LH: {_format_count(preview_record.door_locks_lh)}, "
                f"RH: {_format_count(preview_record.door_locks_rh)})"
            )
            summary_lines.append(
                f"- Operators Produced: {_format_count(preview_record.operators_produced)}"
            )
            summary_lines.append(
                f"- COPs Produced: {_format_count(preview_record.cops_produced)}"
            )

            summary_lines.extend(
                [
                    "",
                    "Gates Labor",
                    f"- Employees: {_format_count(gates_employees)}",
                    f"- OT Hours: {gates_hours_ot}",
                    f"- Total Hours: {gates_total_hours}",
                ]
            )

            if output_variables:
                summary_lines.append("- Output Inputs:")
                for variable in output_variables:
                    summary_lines.append(
                        f"    • {variable.get('label')}: {variable.get('value')}"
                    )

            summary_lines.extend(
                [
                    "",
                    "Additional Labor",
                    f"- Employees: {_format_count(additional_employees)}",
                    f"- OT Hours: {additional_hours_ot}",
                    f"- Total Hours: {additional_total_hours}",
                ]
            )

            if additional_per_hour:
                summary_lines.append("- Output per Hour:")
                for metric in additional_per_hour:
                    summary_lines.append(
                        f"    • {metric.get('label')}: {metric.get('per_hour')}"
                    )

            summary_lines.append("")
            summary_lines.append("Notes")

            notes_text = (preview_record.daily_notes or "").strip()
            if notes_text:
                summary_lines.extend(notes_text.splitlines())
            else:
                summary_lines.append("None recorded.")

            summary_text = "\r\n".join(summary_lines)

        if summary_text is None:
            summary_text = "No production summary is available for the selected date."

        email_preview = {
            "date_iso": preview_date.isoformat(),
            "date_display": date_display,
            "day_of_week": day_of_week,
            "notes": preview_record.daily_notes or "",
            "range_start_display": range_start_display,
            "range_end_display": range_end_display,
            "range_display": range_display,
            "summary_text": summary_text,
        }

    return {
        "customers": table_customers,
        "chart_customers": stack_customers,
        "grouped_customer_names": grouped_names,
        "table_rows": table_rows,
        "chart_labels": chart_labels,
        "stacked_datasets": packaged_stack_datasets,
        "line_datasets": line_datasets,
        "overlay_datasets": overlay_datasets,
        "chart_axis_settings": chart_axis_settings,
        "email_preview": email_preview,
    }


@bp.route("/history")
def history():
    today = date.today()
    start_date = _parse_date(request.args.get("start_date")) or today.replace(day=1)
    end_date = _parse_date(request.args.get("end_date")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    context = _build_history_context(start_date, end_date)
    return render_template(
        "production/history.html",
        start_date=start_date,
        end_date=end_date,
        **context,
    )


@bp.route("/history/export")
def history_export():
    today = date.today()
    start_date = _parse_date(request.args.get("start_date")) or today.replace(day=1)
    end_date = _parse_date(request.args.get("end_date")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    context = _build_history_context(start_date, end_date)
    customers = context["customers"]
    table_rows = context["table_rows"]

    output = io.StringIO()
    writer = csv.writer(output)

    header = [
        "Date",
        "Day",
        "Gates Packaged Total",
        "Gates Combined Total",
    ]
    for customer in customers:
        header.append(f"Gates Packaged - {customer.name}")
    header.extend(
        [
            "Gate Employees",
            "Gate OT Hours",
            "Gate Total Hours",
            "Output per Labor Hour",
            "Output Variables",
            "Controllers Total",
            "Controllers (4 Stop)",
            "Controllers (6 Stop)",
            "Door Locks Total",
            "Door Locks (LH)",
            "Door Locks (RH)",
            "Operators Produced",
            "COPs Produced",
            "Additional Employees",
            "Additional OT Hours",
            "Additional Total Hours",
            "Additional Output Details",
            "Additional Output Total",
            "Notes",
        ]
    )
    writer.writerow(header)

    for row in table_rows:
        record: ProductionDailyRecord = row["record"]
        packaged_breakdown = row["per_customer_packaged"]
        csv_row = [
            record.entry_date.strftime("%Y-%m-%d"),
            record.day_of_week or record.entry_date.strftime("%A"),
            row["packaged_sum"],
            row["gates_combined_total"],
        ]
        for customer in customers:
            csv_row.append(packaged_breakdown.get(customer.id, 0))
        output_variables = "; ".join(
            f"{variable['label']}: {variable['value']}"
            for variable in row["output_variables"]
        )
        additional_details = "; ".join(
            f"{metric['label']}: {metric['per_hour']}"
            for metric in row["additional_per_hour"]
        )
        additional_total = (
            _format_decimal(row["additional_output_total_value"])
            if row["additional_per_hour"]
            else ""
        )
        csv_row.extend(
            [
                row["gates_employees"],
                row["gates_hours_ot"],
                row["gates_total_hours"],
                row["gates_output_per_hour"] or "",
                output_variables,
                row["controllers_total"],
                record.controllers_4_stop or 0,
                record.controllers_6_stop or 0,
                row["door_locks_total"],
                record.door_locks_lh or 0,
                record.door_locks_rh or 0,
                row["operators_total"],
                row["cops_total"],
                row["additional_employees"],
                row["additional_hours_ot"],
                row["additional_total_hours"],
                additional_details,
                additional_total,
                record.daily_notes or "",
            ]
        )
        writer.writerow(csv_row)

    filename = (
        f"production_history_{start_date.isoformat()}_{end_date.isoformat()}.csv"
    )
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={filename}"
    )
    return response


@bp.route("/settings", methods=["GET", "POST"])
def production_settings():
    _ensure_default_customers()
    customers = (
        ProductionCustomer.query.order_by(
            ProductionCustomer.is_other_bucket.asc(), ProductionCustomer.name.asc()
        ).all()
    )
    formula_setting = _ensure_output_formula()
    chart_settings = ProductionChartSettings.get_or_create()
    formula_form_values = {
        "formula": formula_setting.formula,
        "variables": formula_setting.variables or [],
    }

    chart_settings_form_values = {
        "primary_min": _format_optional_decimal(chart_settings.primary_min),
        "primary_max": _format_optional_decimal(chart_settings.primary_max),
        "primary_step": _format_optional_decimal(chart_settings.primary_step),
        "secondary_min": _format_optional_decimal(chart_settings.secondary_min),
        "secondary_max": _format_optional_decimal(chart_settings.secondary_max),
        "secondary_step": _format_optional_decimal(chart_settings.secondary_step),
        "goal_value": _format_optional_decimal(chart_settings.goal_value),
        "show_goal": chart_settings.show_goal,
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

        elif action == "update_chart":
            field_labels = {
                "primary_min": "Primary axis minimum",
                "primary_max": "Primary axis maximum",
                "primary_step": "Primary axis step",
                "secondary_min": "Secondary axis minimum",
                "secondary_max": "Secondary axis maximum",
                "secondary_step": "Secondary axis step",
                "goal_value": "Goal value",
            }
            parsed_values: Dict[str, Decimal | None] = {}
            has_errors = False
            for field, label in field_labels.items():
                raw_value = request.form.get(field)
                parsed = _parse_optional_decimal(raw_value)
                if raw_value not in (None, "") and parsed is None:
                    flash(f"Enter a valid number for {label}.", "error")
                    has_errors = True
                    continue
                parsed_values[field] = parsed

            show_goal_value = request.form.get("show_goal") is not None

            if has_errors:
                chart_settings_form_values = {
                    "primary_min": request.form.get("primary_min", ""),
                    "primary_max": request.form.get("primary_max", ""),
                    "primary_step": request.form.get("primary_step", ""),
                    "secondary_min": request.form.get("secondary_min", ""),
                    "secondary_max": request.form.get("secondary_max", ""),
                    "secondary_step": request.form.get("secondary_step", ""),
                    "goal_value": request.form.get("goal_value", ""),
                    "show_goal": show_goal_value,
                }
            else:
                for field in field_labels:
                    setattr(chart_settings, field, parsed_values[field])
                chart_settings.show_goal = show_goal_value
                db.session.commit()
                flash("Chart settings updated.", "success")
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
        chart_settings_form=chart_settings_form_values,

    )

