from __future__ import annotations

from datetime import datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from invapp.extensions import db
from invapp.login import current_user, login_required
from invapp.models import (
    OpenOrder,
    OpenOrderActionItem,
    OpenOrderLine,
    OpenOrderNote,
    OpenOrderUpload,
    User,
)
from invapp.permissions import principal_has_any_role, resolve_view_roles
from invapp.services.open_orders import (
    add_action_item,
    add_note,
    commit_open_orders_import,
    compute_open_order_diff,
    load_staged_open_orders,
    parse_open_orders,
    stage_open_orders_import,
    clear_staged_open_orders,
    toggle_action_item_done,
)
from invapp.superuser import is_superuser, superuser_required


bp = Blueprint("open_orders", __name__, url_prefix="/open_orders")


def open_orders_view_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if is_superuser():
            return view_func(*args, **kwargs)
        if not principal_has_any_role(resolve_view_roles("open_orders", default_roles=("admin",))):
            abort(403)
        return view_func(*args, **kwargs)

    return login_required(wrapped)


@bp.route("/")
@open_orders_view_required
def open_orders_dashboard():
    status_filter = request.args.get("status", "open").strip().lower()
    query = OpenOrderLine.query

    customer = request.args.get("customer", "").strip()
    so_no = request.args.get("so_no", "").strip()
    item_id = request.args.get("item_id", "").strip()
    internal_status = request.args.get("internal_status", "").strip()
    owner_id = request.args.get("owner_id", "").strip()
    priority = request.args.get("priority", "").strip()
    overdue = request.args.get("overdue", "").strip()

    if status_filter == "open":
        query = query.filter(OpenOrderLine.status == "open")
    elif status_filter == "completed":
        query = query.filter(OpenOrderLine.status == "complete")
    elif status_filter == "all":
        query = query
    else:
        status_filter = "open"
        query = query.filter(OpenOrderLine.status == "open")

    if customer:
        query = query.filter(OpenOrderLine.customer_name.ilike(f"%{customer}%"))
    if so_no:
        query = query.filter(OpenOrderLine.so_no.ilike(f"%{so_no}%"))
    if item_id:
        query = query.filter(OpenOrderLine.item_id.ilike(f"%{item_id}%"))
    if internal_status:
        query = query.filter(OpenOrderLine.internal_status == internal_status)
    if owner_id:
        try:
            query = query.filter(OpenOrderLine.owner_user_id == int(owner_id))
        except ValueError:
            pass
    if priority:
        try:
            query = query.filter(OpenOrderLine.priority == int(priority))
        except ValueError:
            pass
    if overdue:
        today = datetime.utcnow().date()
        query = query.filter(OpenOrderLine.ship_by.isnot(None)).filter(OpenOrderLine.ship_by < today)

    if status_filter == "completed":
        lines = query.order_by(OpenOrderLine.completed_at.desc().nulls_last()).all()
    else:
        lines = query.order_by(OpenOrderLine.ship_by.asc().nulls_last()).all()
    owners = User.query.order_by(User.username.asc()).all()
    internal_status_options = [
        "UNREVIEWED",
        "IN_PROGRESS",
        "BLOCKED",
        "DONE",
    ]

    return render_template(
        "open_orders/dashboard.html",
        lines=lines,
        owners=owners,
        internal_status_options=internal_status_options,
        filters={
            "status": status_filter,
            "customer": customer,
            "so_no": so_no,
            "item_id": item_id,
            "internal_status": internal_status,
            "owner_id": owner_id,
            "priority": priority,
            "overdue": overdue,
        },
    )


@bp.route("/completed")
@open_orders_view_required
def open_orders_completed():
    lines = (
        OpenOrderLine.query.filter(OpenOrderLine.status == "complete")
        .order_by(OpenOrderLine.completed_at.desc().nulls_last())
        .all()
    )
    return render_template("open_orders/completed.html", lines=lines)


@bp.route("/import")
@superuser_required
def open_orders_import():
    last_upload = OpenOrderUpload.query.order_by(OpenOrderUpload.uploaded_at.desc()).first()
    last_summary = None
    if last_upload:
        open_count = (
            OpenOrderLine.query.filter(
                OpenOrderLine.last_seen_upload_id == last_upload.id,
                OpenOrderLine.status != "complete",
            ).count()
        )
        completed_count = OpenOrderLine.query.filter(
            OpenOrderLine.completed_upload_id == last_upload.id
        ).count()
        last_summary = {
            "upload": last_upload,
            "open_count": open_count,
            "completed_count": completed_count,
        }

    return render_template("open_orders/import.html", last_summary=last_summary)


@bp.route("/import/preview", methods=["POST"])
@superuser_required
def preview_open_orders_import():
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        flash("Select an Excel file to import.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    if not uploaded_file.filename.lower().endswith(".xlsx"):
        flash("Upload must be an .xlsx file.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    file_bytes = uploaded_file.read()
    if not file_bytes:
        flash("Uploaded file was empty.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    notes = request.form.get("notes", "").strip() or None
    previous_upload = OpenOrderUpload.query.order_by(OpenOrderUpload.uploaded_at.desc()).first()
    previous_upload_id = previous_upload.id if previous_upload else None

    try:
        current_rows = parse_open_orders(BytesIO(file_bytes))
    except ValueError as exc:
        current_app.logger.warning("Open orders preview failed: %s", exc)
        flash(str(exc), "error")
        return redirect(url_for("open_orders.open_orders_import"))

    previous_open_lines = []
    if previous_upload:
        previous_open_lines = (
            OpenOrderLine.query.filter(
                OpenOrderLine.status != "complete",
                OpenOrderLine.last_seen_upload_id == previous_upload.id,
            )
            .all()
        )

    diff = compute_open_order_diff(current_rows, previous_open_lines)
    token = stage_open_orders_import(file_bytes, uploaded_file.filename, previous_upload_id, notes)

    current_map = {row["natural_key"]: row for row in current_rows}
    new_lines = [current_map[key] for key in sorted(diff.new_keys)]
    still_open_lines = [current_map[key] for key in sorted(diff.still_open_keys)]
    completed_lines = [diff.previous_open_lines[key] for key in sorted(diff.completed_keys)]

    return render_template(
        "open_orders/preview.html",
        filename=uploaded_file.filename,
        token=token,
        diff=diff,
        new_lines=new_lines,
        still_open_lines=still_open_lines,
        completed_lines=completed_lines,
    )


@bp.route("/import/commit", methods=["POST"])
@superuser_required
def commit_open_orders_import_route():
    token = request.form.get("token")
    if not token:
        flash("Missing staged import token.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    try:
        file_bytes, metadata = load_staged_open_orders(token)
    except FileNotFoundError:
        flash("Staged import data not found. Please upload again.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    latest_upload = OpenOrderUpload.query.order_by(OpenOrderUpload.uploaded_at.desc()).first()
    latest_id = latest_upload.id if latest_upload else None
    if metadata.get("previous_upload_id") != latest_id:
        clear_staged_open_orders(token)
        flash("A newer import was detected. Please preview again.", "error")
        return redirect(url_for("open_orders.open_orders_import"))

    try:
        upload = commit_open_orders_import(
            file_bytes,
            metadata.get("filename") or "open_orders.xlsx",
            getattr(current_user, "id", None),
            metadata.get("previous_upload_id"),
            metadata.get("notes"),
        )
    except ValueError as exc:
        current_app.logger.warning("Open orders commit failed: %s", exc)
        flash(str(exc), "error")
        return redirect(url_for("open_orders.open_orders_import"))
    finally:
        clear_staged_open_orders(token)

    flash(f"Open orders import #{upload.id} committed.", "success")
    return redirect(url_for("open_orders.open_orders_dashboard"))


@bp.route("/<int:line_id>/update_workflow", methods=["POST"])
@superuser_required
def update_open_order_workflow(line_id: int):
    line = OpenOrderLine.query.get_or_404(line_id)

    internal_status = request.form.get("internal_status") or "UNREVIEWED"
    owner_id = request.form.get("owner_user_id") or None
    priority = request.form.get("priority") or None
    promised_date = request.form.get("promised_date") or None
    notes = request.form.get("notes")

    line.internal_status = internal_status

    if owner_id:
        try:
            line.owner_user_id = int(owner_id)
        except ValueError:
            line.owner_user_id = None
    else:
        line.owner_user_id = None

    if priority:
        try:
            line.priority = int(priority)
        except ValueError:
            line.priority = None
    else:
        line.priority = None

    if promised_date:
        try:
            line.promised_date = datetime.strptime(promised_date, "%Y-%m-%d").date()
        except ValueError:
            line.promised_date = None
    else:
        line.promised_date = None

    line.notes = notes

    db.session.commit()
    flash("Workflow details updated.", "success")
    return redirect(url_for("open_orders.open_orders_dashboard"))


@bp.route("/orders/<int:order_id>")
@open_orders_view_required
def open_order_detail(order_id: int):
    order = OpenOrder.query.get_or_404(order_id)
    lines = (
        OpenOrderLine.query.filter(OpenOrderLine.order_id == order.id)
        .order_by(OpenOrderLine.ship_by.asc().nulls_last())
        .all()
    )
    notes = order.notes.order_by(OpenOrderNote.created_at.desc()).all()
    action_items = order.action_items.order_by(
        OpenOrderActionItem.is_done.asc(),
        OpenOrderActionItem.sort_order.asc().nulls_last(),
        OpenOrderActionItem.created_at.desc(),
    ).all()
    return render_template(
        "open_orders/order_detail.html",
        order=order,
        lines=lines,
        notes=notes,
        action_items=action_items,
    )


@bp.route("/orders/<int:order_id>/notes", methods=["POST"])
@open_orders_view_required
def add_open_order_note(order_id: int):
    body = request.form.get("body", "").strip()
    if not body:
        flash("Note cannot be empty.", "error")
        return redirect(url_for("open_orders.open_order_detail", order_id=order_id))
    add_note(order_id, getattr(current_user, "id", None), body)
    flash("Note added.", "success")
    return redirect(url_for("open_orders.open_order_detail", order_id=order_id))


@bp.route("/orders/<int:order_id>/action_items", methods=["POST"])
@open_orders_view_required
def add_open_order_action_item(order_id: int):
    title = request.form.get("title", "").strip()
    due_date_raw = request.form.get("due_date")
    if not title:
        flash("Action item title is required.", "error")
        return redirect(url_for("open_orders.open_order_detail", order_id=order_id))
    due_date = None
    if due_date_raw:
        try:
            due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date()
        except ValueError:
            due_date = None
    add_action_item(order_id, getattr(current_user, "id", None), title, due_date=due_date)
    flash("Action item added.", "success")
    return redirect(url_for("open_orders.open_order_detail", order_id=order_id))


@bp.route("/action_items/<int:item_id>/toggle", methods=["POST"])
@open_orders_view_required
def toggle_open_order_action_item(item_id: int):
    is_done = request.form.get("is_done") == "1"
    item = OpenOrderActionItem.query.get_or_404(item_id)
    toggle_action_item_done(item.id, getattr(current_user, "id", None), is_done)
    flash("Action item updated.", "success")
    return redirect(url_for("open_orders.open_order_detail", order_id=item.order_id))
