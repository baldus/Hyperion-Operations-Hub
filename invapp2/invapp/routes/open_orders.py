from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from invapp.extensions import db
from invapp.login import current_user
from invapp.models import (
    OpenOrderInternalStatus,
    OpenOrderLine,
    OpenOrderSystemState,
    OpenOrderUpload,
    User,
)
from invapp.permissions import resolve_view_roles
from invapp.security import require_any_role
from invapp.superuser import superuser_required
from invapp.services.open_orders import (
    OpenOrderImportError,
    build_open_orders_diff,
    import_open_orders,
    parse_open_orders_excel,
    summarize_diff,
)

bp = Blueprint("open_orders", __name__)

STAGED_SESSION_KEY = "open_orders_import"

def require_open_orders_view(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        roles = resolve_view_roles("open_orders")
        return require_any_role(roles)(view_func)(*args, **kwargs)

    return wrapped


@bp.route("/open_orders")
@require_open_orders_view
def open_orders_dashboard():
    query = OpenOrderLine.query.filter(
        OpenOrderLine.system_state.in_(OpenOrderSystemState.ACTIVE_STATES)
    )

    def _parse_int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    customer = request.args.get("customer", "").strip()
    internal_status = request.args.get("internal_status", "").strip()
    owner_id = request.args.get("owner_id", "").strip()
    priority = request.args.get("priority", "").strip()
    item_id = request.args.get("item_id", "").strip()
    so_no = request.args.get("so_no", "").strip()
    overdue = request.args.get("overdue", "") == "1"

    if customer:
        query = query.filter(OpenOrderLine.customer_name.ilike(f"%{customer}%"))
    if internal_status:
        query = query.filter(OpenOrderLine.internal_status == internal_status)
    if owner_id:
        owner_value = _parse_int(owner_id)
        if owner_value is not None:
            query = query.filter(OpenOrderLine.owner_user_id == owner_value)
    if priority:
        priority_value = _parse_int(priority)
        if priority_value is not None:
            query = query.filter(OpenOrderLine.priority == priority_value)
    if item_id:
        query = query.filter(OpenOrderLine.item_id.ilike(f"%{item_id}%"))
    if so_no:
        query = query.filter(OpenOrderLine.so_no.ilike(f"%{so_no}%"))
    if overdue:
        query = query.filter(OpenOrderLine.ship_by < datetime.utcnow().date())

    lines = query.order_by(OpenOrderLine.ship_by.asc().nullslast()).all()

    owners = User.query.order_by(User.username.asc()).all()

    return render_template(
        "open_orders/dashboard.html",
        lines=lines,
        owners=owners,
        internal_statuses=OpenOrderInternalStatus.ALL_STATUSES,
        filters={
            "customer": customer,
            "internal_status": internal_status,
            "owner_id": owner_id,
            "priority": priority,
            "item_id": item_id,
            "so_no": so_no,
            "overdue": overdue,
        },
    )


@bp.route("/open_orders/<int:line_id>")
@require_open_orders_view
def open_orders_detail(line_id: int):
    line = OpenOrderLine.query.get_or_404(line_id)
    owners = User.query.order_by(User.username.asc()).all()
    return render_template(
        "open_orders/detail.html",
        line=line,
        owners=owners,
        internal_statuses=OpenOrderInternalStatus.ALL_STATUSES,
    )


@bp.route("/open_orders/<int:line_id>/update_workflow", methods=["POST"])
@superuser_required
def update_open_order_workflow(line_id: int):
    line = OpenOrderLine.query.get_or_404(line_id)

    internal_status = request.form.get("internal_status") or line.internal_status
    owner_id = request.form.get("owner_user_id") or None
    priority = request.form.get("priority") or None
    promised_date = request.form.get("promised_date") or None
    notes = request.form.get("notes")

    line.internal_status = internal_status
    line.owner_user_id = int(owner_id) if owner_id else None
    line.priority = int(priority) if priority else None
    line.promised_date = (
        datetime.strptime(promised_date, "%Y-%m-%d").date()
        if promised_date
        else None
    )
    line.notes = notes

    db.session.commit()
    flash("Workflow updates saved.", "success")
    return redirect(url_for("open_orders.open_orders_detail", line_id=line.id))


@bp.route("/open_orders/import")
@superuser_required
def open_orders_import():
    last_upload = OpenOrderUpload.query.order_by(OpenOrderUpload.uploaded_at.desc()).first()
    return render_template(
        "open_orders/import.html",
        last_upload=last_upload,
    )


@bp.route("/open_orders/import/preview", methods=["POST"])
@superuser_required
def open_orders_import_preview():
    upload = request.files.get("open_orders_file")
    if upload is None or not upload.filename:
        flash("Select an Excel file to upload.", "danger")
        return redirect(url_for("open_orders.open_orders_import"))

    if not upload.filename.lower().endswith(".xlsx"):
        flash("Only .xlsx files are supported.", "danger")
        return redirect(url_for("open_orders.open_orders_import"))

    file_bytes = upload.read()
    if not file_bytes:
        flash("The uploaded file is empty.", "danger")
        return redirect(url_for("open_orders.open_orders_import"))

    try:
        rows = parse_open_orders_excel(io.BytesIO(file_bytes))
        diff = build_open_orders_diff(rows)
        summary = summarize_diff(diff)
    except OpenOrderImportError as exc:
        current_app.logger.warning("Open order import preview failed: %s", exc)
        flash(str(exc), "danger")
        return redirect(url_for("open_orders.open_orders_import"))

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".xlsx",
        prefix="open_orders_",
    )
    temp_file.write(file_bytes)
    temp_file.flush()
    temp_file.close()

    token = os.urandom(16).hex()
    session[STAGED_SESSION_KEY] = {
        "token": token,
        "path": temp_file.name,
        "filename": upload.filename,
    }

    return render_template(
        "open_orders/preview.html",
        summary=summary,
        token=token,
        filename=upload.filename,
    )


@bp.route("/open_orders/import/commit", methods=["POST"])
@superuser_required
def open_orders_import_commit():
    staged = session.get(STAGED_SESSION_KEY)
    token = request.form.get("token")
    if not staged or staged.get("token") != token:
        flash("No staged import found. Please upload the file again.", "danger")
        return redirect(url_for("open_orders.open_orders_import"))

    path = staged.get("path")
    filename = staged.get("filename")
    if not path or not os.path.exists(path):
        flash("The staged import file is missing. Please upload again.", "danger")
        session.pop(STAGED_SESSION_KEY, None)
        return redirect(url_for("open_orders.open_orders_import"))

    try:
        with open(path, "rb") as handle:
            result = import_open_orders(io.BytesIO(handle.read()), filename, current_user)
    except OpenOrderImportError as exc:
        current_app.logger.warning("Open order import failed: %s", exc)
        flash(str(exc), "danger")
        return redirect(url_for("open_orders.open_orders_import"))
    finally:
        try:
            os.unlink(path)
        except OSError:
            current_app.logger.warning("Unable to remove staged file %s", path)
        session.pop(STAGED_SESSION_KEY, None)

    flash(
        (
            f"Open orders import completed. New: {len(result.diff.new_keys)}, "
            f"Still open: {len(result.diff.still_open_keys)}, "
            f"Completed: {len(result.diff.completed_keys)}."
        ),
        "success",
    )
    return redirect(url_for("open_orders.open_orders_dashboard"))
