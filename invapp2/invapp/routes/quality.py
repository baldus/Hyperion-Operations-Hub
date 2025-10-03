from __future__ import annotations

import os
from datetime import date, datetime
from functools import wraps
from typing import Iterable

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.orm.exc import DetachedInstanceError
from werkzeug.utils import secure_filename

from invapp.auth import blueprint_page_guard
from invapp.login import current_user
from invapp.models import RMAAttachment, RMARequest, RMAStatusEvent, User, db
from invapp.permissions import resolve_edit_roles
from invapp.security import require_any_role


bp = Blueprint("quality", __name__, url_prefix="/quality")

bp.before_request(blueprint_page_guard("quality"))


def _clean_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _parse_date(value: str, *, field_label: str) -> tuple[date | None, str | None]:
    text = (value or "").strip()
    if not text:
        return None, None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None, f"Enter {field_label} in YYYY-MM-DD format."
    return parsed, None


def _require_quality_edit(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        edit_roles = resolve_edit_roles("quality", default_roles=("admin", "quality"))
        guard = require_any_role(edit_roles)
        return guard(view_func)(*args, **kwargs)

    return wrapped


def _current_actor() -> str:
    if not current_user.is_authenticated:
        return "system"
    try:
        username = getattr(current_user, "username", None)
        if username:
            return username
    except DetachedInstanceError:
        pass

    identity = session.get("_user_id")
    if identity is None:
        try:
            identity = getattr(current_user, "id", None)
        except DetachedInstanceError:
            identity = None

    if identity is None:
        return "system"

    try:
        identity_value = int(identity)
    except (TypeError, ValueError):
        identity_value = identity

    refreshed = db.session.get(User, identity_value)
    if refreshed is not None and refreshed.username:
        return refreshed.username
    return "system"


def _allowed_rma_attachment(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    allowed = current_app.config.get("QUALITY_ATTACHMENT_ALLOWED_EXTENSIONS", set())
    return extension in allowed


def _save_rma_attachment(request_record: RMARequest, file_storage):
    if not file_storage or not file_storage.filename:
        return False, "Select a file to upload.", None

    filename = file_storage.filename
    if not _allowed_rma_attachment(filename):
        allowed = current_app.config.get("QUALITY_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        allowed_list = ", ".join(sorted(allowed)) if allowed else "(none)"
        return False, f"Attachment not saved. Allowed file types: {allowed_list}", None

    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = f"attachment_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    upload_folder = current_app.config.get("QUALITY_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        return False, "Attachment upload folder is not configured.", None

    os.makedirs(upload_folder, exist_ok=True)
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{os.urandom(6).hex()}_{safe_name}"
    file_path = os.path.join(upload_folder, unique_name)
    file_storage.save(file_path)

    attachment = RMAAttachment(
        request=request_record,
        filename=unique_name,
        original_name=safe_name,
    )
    db.session.add(attachment)
    return True, None, attachment


@bp.route("/")
def quality_home():
    status_filter = (request.args.get("status") or "").strip().lower()
    valid_statuses = set(RMARequest.status_values())
    query = RMARequest.query.order_by(RMARequest.opened_at.desc())

    if status_filter == "open":
        query = query.filter(~RMARequest.status.in_(RMARequest.CLOSED_STATUSES))
    elif status_filter in valid_statuses:
        query = query.filter(RMARequest.status == status_filter)
    elif status_filter:
        flash("Unknown status filter applied. Showing all requests.", "warning")

    requests = query.all()

    raw_counts: Iterable[tuple[str, int]] = (
        db.session.query(RMARequest.status, func.count(RMARequest.id))
        .group_by(RMARequest.status)
        .all()
    )
    status_counts = {status: count for status, count in raw_counts}
    open_count = sum(
        count for status, count in status_counts.items() if status not in RMARequest.CLOSED_STATUSES
    )

    return render_template(
        "quality/home.html",
        requests=requests,
        status_filter=status_filter,
        status_choices=RMARequest.STATUS_CHOICES,
        status_labels=dict(RMARequest.STATUS_CHOICES),
        status_counts=status_counts,
        open_count=open_count,
        priority_labels=dict(RMARequest.PRIORITY_CHOICES),
    )


@bp.route("/requests/new", methods=["GET", "POST"])
@_require_quality_edit
def new_request():
    default_priority = RMARequest.PRIORITY_NORMAL
    form_data = {
        "customer_name": "",
        "customer_contact": "",
        "customer_reference": "",
        "product_sku": "",
        "product_description": "",
        "product_serial": "",
        "issue_category": "",
        "issue_description": "",
        "requested_action": "",
        "priority": default_priority,
        "target_resolution_date": "",
        "last_customer_contact": "",
        "follow_up_tasks": "",
        "internal_notes": "",
    }

    if request.method == "POST":
        for field in form_data:
            form_data[field] = request.form.get(field, "").strip()

        errors: list[str] = []
        if not form_data["customer_name"]:
            errors.append("Customer name is required.")
        if not form_data["issue_description"]:
            errors.append("Provide a description of the issue or defect.")

        priority_value = form_data.get("priority") or default_priority
        if priority_value not in dict(RMARequest.PRIORITY_CHOICES):
            errors.append("Select a valid priority level.")

        target_resolution, target_error = _parse_date(
            form_data["target_resolution_date"], field_label="the target resolution date"
        )
        if target_error:
            errors.append(target_error)

        last_contact, last_contact_error = _parse_date(
            form_data["last_customer_contact"], field_label="the last customer contact date"
        )
        if last_contact_error:
            errors.append(last_contact_error)

        if errors:
            for message in errors:
                flash(message, "error")
        else:
            opened_by = _current_actor()
            rma_request = RMARequest(
                customer_name=form_data["customer_name"],
                customer_contact=_clean_text(form_data["customer_contact"]),
                customer_reference=_clean_text(form_data["customer_reference"]),
                product_sku=_clean_text(form_data["product_sku"]),
                product_description=_clean_text(form_data["product_description"]),
                product_serial=_clean_text(form_data["product_serial"]),
                issue_category=_clean_text(form_data["issue_category"]),
                issue_description=form_data["issue_description"].strip(),
                requested_action=_clean_text(form_data["requested_action"]),
                priority=priority_value,
                target_resolution_date=target_resolution,
                last_customer_contact=last_contact,
                follow_up_tasks=_clean_text(form_data["follow_up_tasks"]),
                internal_notes=_clean_text(form_data["internal_notes"]),
                opened_by=opened_by,
            )
            rma_request.status_events.append(
                RMAStatusEvent(
                    from_status=None,
                    to_status=rma_request.status,
                    changed_by=opened_by,
                    note="Request created",
                )
            )
            db.session.add(rma_request)
            db.session.commit()
            flash("RMA request logged for quality review.", "success")
            return redirect(url_for("quality.view_request", request_id=rma_request.id))

    return render_template(
        "quality/new_request.html",
        form_data=form_data,
        priority_choices=RMARequest.PRIORITY_CHOICES,
    )


@bp.route("/requests/<int:request_id>")
def view_request(request_id: int):
    rma_request = RMARequest.query.get_or_404(request_id)
    allowed_extensions = sorted(
        current_app.config.get("QUALITY_ATTACHMENT_ALLOWED_EXTENSIONS", set())
    )
    return render_template(
        "quality/detail.html",
        rma_request=rma_request,
        status_choices=RMARequest.STATUS_CHOICES,
        priority_choices=RMARequest.PRIORITY_CHOICES,
        status_labels=dict(RMARequest.STATUS_CHOICES),
        priority_labels=dict(RMARequest.PRIORITY_CHOICES),
        allowed_extensions=allowed_extensions,
    )


@bp.route("/requests/<int:request_id>/update", methods=["POST"])
@_require_quality_edit
def update_request(request_id: int):
    rma_request = RMARequest.query.get_or_404(request_id)

    status_value = (request.form.get("status") or rma_request.status).strip()
    note = _clean_text(request.form.get("note"))
    resolution = _clean_text(request.form.get("resolution"))
    follow_up_tasks = _clean_text(request.form.get("follow_up_tasks"))
    internal_notes = _clean_text(request.form.get("internal_notes"))
    requested_action = _clean_text(request.form.get("requested_action"))
    return_tracking_number = _clean_text(request.form.get("return_tracking_number"))
    replacement_order_number = _clean_text(request.form.get("replacement_order_number"))
    customer_contact = _clean_text(request.form.get("customer_contact"))
    customer_reference = _clean_text(request.form.get("customer_reference"))
    customer_name = (request.form.get("customer_name") or "").strip()
    issue_description = (request.form.get("issue_description") or "").strip()
    issue_category = _clean_text(request.form.get("issue_category"))
    product_sku = _clean_text(request.form.get("product_sku"))
    product_description = _clean_text(request.form.get("product_description"))
    product_serial = _clean_text(request.form.get("product_serial"))
    priority_value = (request.form.get("priority") or rma_request.priority).strip()

    target_resolution, target_error = _parse_date(
        request.form.get("target_resolution_date", ""),
        field_label="the target resolution date",
    )
    last_contact, last_contact_error = _parse_date(
        request.form.get("last_customer_contact", ""),
        field_label="the last customer contact date",
    )

    errors: list[str] = []
    if status_value not in RMARequest.status_values():
        errors.append("Choose a valid status for the RMA.")
    if priority_value not in dict(RMARequest.PRIORITY_CHOICES):
        errors.append("Select a valid priority level.")
    if not customer_name:
        errors.append("Customer name cannot be empty.")
    if not issue_description:
        errors.append("Issue description cannot be empty.")
    if target_error:
        errors.append(target_error)
    if last_contact_error:
        errors.append(last_contact_error)

    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("quality.view_request", request_id=rma_request.id))

    changes: list[str] = []
    old_status = rma_request.status
    old_resolution = rma_request.resolution
    old_follow_up = rma_request.follow_up_tasks
    old_internal_notes = rma_request.internal_notes
    old_priority = rma_request.priority
    old_requested_action = rma_request.requested_action
    old_return_tracking = rma_request.return_tracking_number
    old_replacement_order = rma_request.replacement_order_number
    old_target_date = rma_request.target_resolution_date
    old_last_contact = rma_request.last_customer_contact

    if status_value != rma_request.status:
        rma_request.mark_status(status_value)

    if priority_value != old_priority:
        changes.append(
            "Priority set to {} (was {}).".format(
                RMARequest.priority_label(priority_value),
                RMARequest.priority_label(old_priority),
            )
        )
        rma_request.priority = priority_value

    rma_request.customer_name = customer_name
    rma_request.customer_contact = customer_contact
    rma_request.customer_reference = customer_reference
    rma_request.issue_description = issue_description
    rma_request.issue_category = issue_category
    rma_request.product_sku = product_sku
    rma_request.product_description = product_description
    rma_request.product_serial = product_serial

    if requested_action != old_requested_action:
        change_text = (
            "Requested action cleared." if not requested_action else "Requested action updated."
        )
        changes.append(change_text)
        rma_request.requested_action = requested_action
    else:
        rma_request.requested_action = requested_action

    if resolution != old_resolution:
        change_text = "Resolution cleared." if not resolution else "Resolution updated."
        changes.append(change_text)
        rma_request.resolution = resolution
    else:
        rma_request.resolution = resolution

    if follow_up_tasks != old_follow_up:
        change_text = "Follow-up tasks cleared." if not follow_up_tasks else "Follow-up tasks updated."
        changes.append(change_text)
        rma_request.follow_up_tasks = follow_up_tasks
    else:
        rma_request.follow_up_tasks = follow_up_tasks

    if internal_notes != old_internal_notes:
        change_text = "Internal notes cleared." if not internal_notes else "Internal notes updated."
        changes.append(change_text)
        rma_request.internal_notes = internal_notes
    else:
        rma_request.internal_notes = internal_notes

    if return_tracking_number != old_return_tracking:
        change_text = (
            "Return tracking cleared."
            if not return_tracking_number
            else "Return tracking number updated."
        )
        changes.append(change_text)
        rma_request.return_tracking_number = return_tracking_number
    else:
        rma_request.return_tracking_number = return_tracking_number

    if replacement_order_number != old_replacement_order:
        change_text = (
            "Replacement order cleared."
            if not replacement_order_number
            else "Replacement order number updated."
        )
        changes.append(change_text)
        rma_request.replacement_order_number = replacement_order_number
    else:
        rma_request.replacement_order_number = replacement_order_number

    if target_resolution != old_target_date:
        if target_resolution is None:
            changes.append("Target resolution date cleared.")
        else:
            changes.append(
                f"Target resolution date set to {target_resolution.isoformat()}."
            )
        rma_request.target_resolution_date = target_resolution
    else:
        rma_request.target_resolution_date = target_resolution

    if last_contact != old_last_contact:
        if last_contact is None:
            changes.append("Last customer contact date cleared.")
        else:
            changes.append(
                f"Last customer contact noted as {last_contact.isoformat()}."
            )
        rma_request.last_customer_contact = last_contact
    else:
        rma_request.last_customer_contact = last_contact

    change_summary = [entry for entry in changes if entry]
    if note:
        change_summary.insert(0, note)

    if old_status != rma_request.status or change_summary:
        changed_by = _current_actor()
        rma_request.status_events.append(
            RMAStatusEvent(
                from_status=old_status if old_status != rma_request.status else None,
                to_status=rma_request.status,
                note="\n".join(change_summary) if change_summary else None,
                changed_by=changed_by,
            )
        )

    db.session.commit()
    flash("RMA request updated.", "success")
    return redirect(url_for("quality.view_request", request_id=rma_request.id))


@bp.route("/requests/<int:request_id>/attachments", methods=["POST"])
@_require_quality_edit
def upload_attachment(request_id: int):
    rma_request = RMARequest.query.get_or_404(request_id)
    file_storage = request.files.get("attachment")

    success, message, attachment = _save_rma_attachment(rma_request, file_storage)
    if not success:
        flash(message or "Attachment not uploaded.", "error")
        return redirect(url_for("quality.view_request", request_id=rma_request.id))

    changed_by = _current_actor()
    rma_request.status_events.append(
        RMAStatusEvent(
            from_status=None,
            to_status=rma_request.status,
            note=f"Attachment added: {attachment.original_name}",
            changed_by=changed_by,
        )
    )
    db.session.commit()
    flash("Attachment uploaded.", "success")
    return redirect(url_for("quality.view_request", request_id=rma_request.id))


@bp.route(
    "/requests/<int:request_id>/attachments/<int:attachment_id>/download",
    methods=["GET"],
)
def download_attachment(request_id: int, attachment_id: int):
    attachment = (
        RMAAttachment.query.filter_by(id=attachment_id, request_id=request_id).first()
    )
    if attachment is None:
        abort(404)

    upload_folder = current_app.config.get("QUALITY_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        abort(404)

    return send_from_directory(
        upload_folder,
        attachment.filename,
        as_attachment=True,
        download_name=attachment.original_name,
    )


@bp.route(
    "/requests/<int:request_id>/attachments/<int:attachment_id>/delete",
    methods=["POST"],
)
@_require_quality_edit
def delete_attachment(request_id: int, attachment_id: int):
    attachment = (
        RMAAttachment.query.filter_by(id=attachment_id, request_id=request_id).first()
    )
    if attachment is None:
        abort(404)

    upload_folder = current_app.config.get("QUALITY_ATTACHMENT_UPLOAD_FOLDER")
    if upload_folder:
        file_path = os.path.join(upload_folder, attachment.filename)
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        except OSError:
            flash("Attachment removed from record, but the file could not be deleted.", "warning")

    changed_by = _current_actor()
    attachment.request.status_events.append(
        RMAStatusEvent(
            from_status=None,
            to_status=attachment.request.status,
            note=f"Attachment removed: {attachment.original_name}",
            changed_by=changed_by,
        )
    )

    db.session.delete(attachment)
    db.session.commit()
    flash("Attachment removed.", "success")
    return redirect(url_for("quality.view_request", request_id=request_id))
