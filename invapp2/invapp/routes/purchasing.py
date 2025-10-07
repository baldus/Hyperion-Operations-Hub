"""Views for requesting and tracking purchased materials."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Iterable

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from werkzeug.routing import BuildError
from sqlalchemy import func

from invapp.auth import blueprint_page_guard
from invapp.login import current_user
from invapp.models import PurchaseRequest, db
from invapp.permissions import resolve_edit_roles
from invapp.security import require_any_role


bp = Blueprint("purchasing", __name__, url_prefix="/purchasing")

bp.before_request(blueprint_page_guard("purchasing"))

CLOSED_STATUSES = {
    PurchaseRequest.STATUS_RECEIVED,
    PurchaseRequest.STATUS_CANCELLED,
}


def _parse_decimal(value: str) -> tuple[Decimal | None, str | None]:
    text = (value or "").strip()
    if not text:
        return None, None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, "Enter a valid numeric quantity."
    return number.quantize(Decimal("0.01")), None


def _parse_date(value: str, *, field_label: str) -> tuple[date | None, str | None]:
    text = (value or "").strip()
    if not text:
        return None, None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None, f"Enter {field_label} in YYYY-MM-DD format."
    return parsed, None


def _clean_text(value: str) -> str | None:
    text = (value or "").strip()
    return text or None


def _format_decimal_for_number_field(value: Decimal) -> str:
    """Return a clean string for Decimal quantities used in number inputs."""

    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _extract_sku_from_title(title: str | None) -> str | None:
    """Attempt to pull an item SKU from a purchase request title."""

    if not title:
        return None

    separators = [" – ", " — ", " - ", "–", "—", "-"]
    for separator in separators:
        if separator in title:
            candidate = title.split(separator, 1)[0].strip()
            if candidate:
                return candidate

    cleaned = title.strip()
    if cleaned and " " not in cleaned:
        return cleaned
    return None


def _require_purchasing_edit(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        edit_roles = resolve_edit_roles(
            "purchasing", default_roles=("editor", "admin", "purchasing")
        )
        guard = require_any_role(edit_roles)
        return guard(view_func)(*args, **kwargs)

    return wrapped


@bp.route("/")
def purchasing_home():
    status_filter = (request.args.get("status") or "").strip().lower()
    valid_statuses = set(PurchaseRequest.status_values())
    query = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc())

    if status_filter == "open":
        query = query.filter(~PurchaseRequest.status.in_(CLOSED_STATUSES))
    elif status_filter in valid_statuses:
        query = query.filter(PurchaseRequest.status == status_filter)
    elif status_filter:
        flash("Unknown status filter applied. Showing all requests.", "warning")

    requests = query.all()

    raw_counts: Iterable[tuple[str, int]] = (
        db.session.query(PurchaseRequest.status, func.count(PurchaseRequest.id))
        .group_by(PurchaseRequest.status)
        .all()
    )
    status_counts = {status: count for status, count in raw_counts}
    open_count = sum(
        count for status, count in status_counts.items() if status not in CLOSED_STATUSES
    )

    return render_template(
        "purchasing/home.html",
        requests=requests,
        status_filter=status_filter,
        status_choices=PurchaseRequest.STATUS_CHOICES,
        status_counts=status_counts,
        open_count=open_count,
        status_labels=dict(PurchaseRequest.STATUS_CHOICES),
    )


@bp.route("/new", methods=["GET", "POST"])
@_require_purchasing_edit
def new_request():
    default_requestor = current_user.username if current_user.is_authenticated else ""
    form_data = {
        "title": "",
        "description": "",
        "quantity": "",
        "unit": "",
        "needed_by": "",
        "supplier_name": "",
        "supplier_contact": "",
        "notes": "",
        "requested_by": default_requestor,
    }

    if request.method == "POST":
        for field in form_data:
            form_data[field] = request.form.get(field, "").strip()

        errors: list[str] = []
        if not form_data["title"]:
            errors.append("An item, part, or material description is required.")

        quantity_value, quantity_error = _parse_decimal(form_data["quantity"])
        if quantity_error:
            errors.append(quantity_error)

        needed_by_value, needed_by_error = _parse_date(
            form_data["needed_by"], field_label="the needed-by date"
        )
        if needed_by_error:
            errors.append(needed_by_error)

        requestor = form_data["requested_by"] or default_requestor
        if not requestor:
            errors.append("Identify who is requesting the purchase.")

        if errors:
            for message in errors:
                flash(message, "error")
        else:
            purchase_request = PurchaseRequest(
                title=form_data["title"],
                description=_clean_text(form_data["description"]),
                quantity=quantity_value,
                unit=_clean_text(form_data["unit"]),
                requested_by=requestor,
                needed_by=needed_by_value,
                supplier_name=_clean_text(form_data["supplier_name"]),
                supplier_contact=_clean_text(form_data["supplier_contact"]),
                notes=_clean_text(form_data["notes"]),
            )
            try:
                PurchaseRequest.commit_with_sequence_retry(purchase_request)
            except Exception:
                current_app.logger.exception(
                    "Failed to create purchase request from purchasing form"
                )
                raise
            flash("Purchase request logged for purchasing review.", "success")
            return redirect(
                url_for("purchasing.view_request", request_id=purchase_request.id)
            )

    return render_template(
        "purchasing/new.html",
        form_data=form_data,
    )


@bp.route("/<int:request_id>")
def view_request(request_id: int):
    purchase_request = PurchaseRequest.query.get_or_404(request_id)
    receiving_params: dict[str, str] = {}

    sku = _extract_sku_from_title(purchase_request.title)
    if sku:
        receiving_params["sku"] = sku

    if purchase_request.quantity is not None:
        receiving_params["qty"] = _format_decimal_for_number_field(
            purchase_request.quantity
        )

    if purchase_request.requested_by:
        receiving_params["person"] = purchase_request.requested_by

    if purchase_request.purchase_order_number:
        receiving_params["po_number"] = purchase_request.purchase_order_number

    try:
        receive_url = (
            url_for("inventory.receiving", **receiving_params)
            if receiving_params
            else url_for("inventory.receiving")
        )
    except BuildError:
        receive_url = None
    return render_template(
        "purchasing/detail.html",
        purchase_request=purchase_request,
        status_choices=PurchaseRequest.STATUS_CHOICES,
        status_labels=dict(PurchaseRequest.STATUS_CHOICES),
        receive_url=receive_url,
    )


@bp.route("/<int:request_id>/update", methods=["POST"])
@_require_purchasing_edit
def update_request(request_id: int):
    purchase_request = PurchaseRequest.query.get_or_404(request_id)

    errors: list[str] = []
    status = (request.form.get("status") or purchase_request.status).strip().lower()
    try:
        purchase_request.mark_status(status)
    except ValueError:
        errors.append("Choose a valid status.")

    purchase_request.title = _clean_text(request.form.get("title")) or purchase_request.title
    purchase_request.description = _clean_text(request.form.get("description"))
    purchase_request.unit = _clean_text(request.form.get("unit"))
    purchase_request.supplier_name = _clean_text(request.form.get("supplier_name"))
    purchase_request.supplier_contact = _clean_text(request.form.get("supplier_contact"))
    purchase_request.notes = _clean_text(request.form.get("notes"))
    purchase_request.purchase_order_number = _clean_text(
        request.form.get("purchase_order_number")
    )
    purchase_request.requested_by = (
        _clean_text(request.form.get("requested_by")) or purchase_request.requested_by
    )

    quantity_value, quantity_error = _parse_decimal(request.form.get("quantity", ""))
    if quantity_error:
        errors.append(quantity_error)
    else:
        purchase_request.quantity = quantity_value

    needed_by_value, needed_by_error = _parse_date(
        request.form.get("needed_by", ""), field_label="the needed-by date"
    )
    if needed_by_error:
        errors.append(needed_by_error)
    else:
        purchase_request.needed_by = needed_by_value

    eta_value, eta_error = _parse_date(
        request.form.get("eta_date", ""), field_label="the ETA"
    )
    if eta_error:
        errors.append(eta_error)
    else:
        purchase_request.eta_date = eta_value

    if errors:
        for message in errors:
            flash(message, "error")
        db.session.rollback()
    else:
        db.session.commit()
        flash("Purchase request updated.", "success")

    return redirect(url_for("purchasing.view_request", request_id=request_id))
