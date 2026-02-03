"""Views for requesting and tracking purchased materials."""

from __future__ import annotations

from datetime import date, datetime
import json
import os
import secrets
import uuid
from decimal import Decimal, InvalidOperation
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
from werkzeug.routing import BuildError
from werkzeug.utils import secure_filename
from sqlalchemy import func, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import DetachedInstanceError

from invapp.auth import blueprint_page_guard
from invapp.extensions import login_manager
from invapp.login import current_user
from invapp.models import (
    Item,
    PurchaseRequest,
    PurchaseRequestAttachment,
    PurchaseRequestDeleteAudit,
    User,
    db,
)
from invapp.permissions import resolve_edit_roles
from invapp.security import require_any_role
from invapp.superuser import is_superuser


bp = Blueprint("purchasing", __name__, url_prefix="/purchasing")

bp.before_request(blueprint_page_guard("purchasing"))

CLOSED_STATUSES = {
    PurchaseRequest.STATUS_RECEIVED,
    PurchaseRequest.STATUS_CANCELLED,
}

PURCHASING_SHORTAGE_DEFAULT_COLUMNS = [
    "id",
    "title",
    "quantity",
    "needed_by",
    "status",
    "supplier_name",
    "eta_date",
    "requested_by",
    "updated_at",
]

PURCHASING_SHORTAGE_COLUMN_LABEL_OVERRIDES = {
    "id": "ID",
    "title": "Item / Description",
    "eta_date": "ETA",
    "needed_by": "Needed By",
    "purchase_order_number": "PO Number",
    "requested_by": "Requested By",
    "supplier_name": "Supplier",
    "updated_at": "Updated",
    "created_at": "Created",
}

PURCHASING_SHORTAGE_TEXT_LIMIT = 80


def _humanize_column_label(column_name: str) -> str:
    words = column_name.replace("_", " ").strip()
    if not words:
        return column_name
    return words.title()


def get_purchase_request_column_defs() -> list[dict[str, str]]:
    columns: list[dict[str, str]] = []
    for column in PurchaseRequest.__table__.columns:
        key = column.name
        label = PURCHASING_SHORTAGE_COLUMN_LABEL_OVERRIDES.get(
            key, _humanize_column_label(key)
        )
        columns.append({"key": key, "label": label})
    return columns


def _coerce_user_column_pref(
    raw_pref: object,
    *,
    allowed_keys: set[str],
    default_keys: list[str],
) -> list[str]:
    if not raw_pref:
        return list(default_keys)

    parsed: object
    if isinstance(raw_pref, str):
        try:
            parsed = json.loads(raw_pref)
        except (TypeError, ValueError):
            return list(default_keys)
    else:
        parsed = raw_pref

    if not isinstance(parsed, (list, tuple)):
        return list(default_keys)

    ordered: list[str] = []
    seen: set[str] = set()
    for key in parsed:
        if not isinstance(key, str) or key not in allowed_keys or key in seen:
            continue
        ordered.append(key)
        seen.add(key)

    return ordered or list(default_keys)


def _format_purchase_request_value(value: object) -> dict[str, str | None]:
    if value is None:
        return {"text": "—", "title": None}

    if isinstance(value, bool):
        return {"text": "Yes" if value else "No", "title": None}

    if isinstance(value, datetime):
        return {"text": value.strftime("%Y-%m-%d %H:%M"), "title": None}

    if isinstance(value, date):
        return {"text": value.isoformat(), "title": None}

    if isinstance(value, Decimal):
        return {"text": _format_decimal_for_number_field(value), "title": None}

    text = str(value)
    if not text:
        return {"text": "—", "title": None}

    if len(text) <= PURCHASING_SHORTAGE_TEXT_LIMIT:
        return {"text": text, "title": None}

    truncated = f"{text[:PURCHASING_SHORTAGE_TEXT_LIMIT - 1]}…"
    return {"text": truncated, "title": text}


def _purchase_request_attr(purchase_request: PurchaseRequest, key: str) -> object:
    return getattr(purchase_request, key, None)


def _current_user_shortage_pref() -> object:
    if not current_user.is_authenticated:
        return None
    try:
        return current_user.purchasing_shortage_columns
    except DetachedInstanceError:
        user_id = session.get("_user_id")
        if not user_id:
            return None
        refreshed = db.session.get(User, int(user_id))
        if refreshed is None:
            return None
        return refreshed.purchasing_shortage_columns


def _shortage_columns_return_target(default: str) -> str:
    target = (request.form.get("return_to") or "").strip()
    if target.startswith("/purchasing"):
        return target
    return default


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


def purchase_request_form_defaults(default_requestor: str) -> dict[str, str]:
    return {
        "item_id": "",
        "item_number": "",
        "item_name": "",
        "item_description": "",
        "title": "",
        "description": "",
        "quantity": "",
        "unit": "",
        "needed_by": "",
        "requested_by": default_requestor,
        "supplier_name": "",
        "supplier_contact": "",
        "eta_date": "",
        "purchase_order_number": "",
        "notes": "",
    }


def _resolve_item_from_form(form: dict[str, str]) -> Item | None:
    raw_item_id = (form.get("item_id") or "").strip()
    if not raw_item_id:
        return None
    try:
        item_id = int(raw_item_id)
    except ValueError:
        return None
    if item_id <= 0:
        return None
    return db.session.get(Item, item_id)


def build_purchase_request_from_form(
    form: dict[str, str],
    *,
    default_requestor: str,
) -> tuple[PurchaseRequest | None, list[str], dict[str, str]]:
    """Shared builder for purchase requests (purchasing + MDI materials)."""

    form_data = purchase_request_form_defaults(default_requestor)
    for field in form_data:
        form_data[field] = (form.get(field, "") or "").strip()

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

    eta_value, eta_error = _parse_date(form_data["eta_date"], field_label="the ETA")
    if eta_error:
        errors.append(eta_error)

    requestor = form_data["requested_by"] or default_requestor
    form_data["requested_by"] = requestor
    if not requestor:
        errors.append("Identify who is requesting the purchase.")

    if errors:
        return None, errors, form_data

    selected_item = _resolve_item_from_form(form)
    item_number = _clean_text(form_data["item_number"])
    if selected_item is not None:
        item_number = selected_item.sku
    elif not item_number:
        item_number = _extract_sku_from_title(form_data["title"]) or form_data["title"]

    purchase_request = PurchaseRequest(
        item_id=selected_item.id if selected_item else None,
        item_number=_clean_text(item_number),
        title=form_data["title"],
        description=_clean_text(form_data["description"]),
        quantity=quantity_value,
        unit=_clean_text(form_data["unit"]),
        requested_by=requestor,
        needed_by=needed_by_value,
        supplier_name=_clean_text(form_data["supplier_name"]),
        supplier_contact=_clean_text(form_data["supplier_contact"]),
        eta_date=eta_value,
        purchase_order_number=_clean_text(form_data["purchase_order_number"]),
        notes=_clean_text(form_data["notes"]),
    )
    return purchase_request, [], form_data


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


def _current_username() -> str | None:
    if not current_user.is_authenticated:
        return None
    try:
        return getattr(current_user, "username", None)
    except DetachedInstanceError:
        identity = inspect(current_user).identity
        if not identity:
            return None
        user = db.session.get(User, identity[0])
        return getattr(user, "username", None) if user else None
    except Exception:
        user_id = _current_user_id()
        if user_id is None:
            return None
        user = db.session.get(User, user_id)
        return getattr(user, "username", None) if user else None


def _current_actor() -> str:
    return _current_username() or "system"


def _current_user_id() -> int | None:
    if not current_user.is_authenticated:
        return None
    try:
        user_id = current_user.get_id()
    except Exception:
        return None
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _allowed_purchase_attachment(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    allowed = current_app.config.get("PURCHASING_ATTACHMENT_ALLOWED_EXTENSIONS", set())
    return extension in allowed


def _file_storage_size(file_storage) -> int:
    if not file_storage:
        return 0
    size = file_storage.content_length
    if size is not None:
        return size
    stream = file_storage.stream
    if not stream or not hasattr(stream, "seek"):
        return 0
    try:
        current_pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current_pos)
        return size
    except OSError:
        return 0


def _delete_request_csrf_token() -> str:
    token = session.get("purchase_request_delete_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["purchase_request_delete_csrf"] = token
    return token


def _delete_request_csrf_valid(token: str | None) -> bool:
    return bool(token) and token == session.get("purchase_request_delete_csrf")


def _require_superuser_delete():
    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    if not is_superuser():
        flash("Delete is restricted to the system superuser.", "danger")
        return (
            render_template(
                "errors/forbidden.html",
                message="Item shortage deletes are restricted to the system superuser.",
            ),
            403,
        )

    return None


def _save_purchase_attachment(purchase_request: PurchaseRequest, file_storage):
    if not file_storage or not file_storage.filename:
        return False, "Select a file to upload.", None

    filename = file_storage.filename
    if not _allowed_purchase_attachment(filename):
        allowed = current_app.config.get("PURCHASING_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        allowed_list = ", ".join(sorted(allowed)) if allowed else "(none)"
        return (
            False,
            f"Attachment not saved. Allowed file types: {allowed_list}",
            None,
        )

    max_size_mb = current_app.config.get("PURCHASING_ATTACHMENT_MAX_SIZE_MB", 25)
    max_size_bytes = max_size_mb * 1024 * 1024
    file_size = _file_storage_size(file_storage)
    if file_size and file_size > max_size_bytes:
        return (
            False,
            f"Attachment exceeds the {max_size_mb} MB upload limit.",
            None,
        )

    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = f"attachment_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    upload_folder = current_app.config.get("PURCHASING_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        return False, "Attachment upload folder is not configured.", None

    os.makedirs(upload_folder, exist_ok=True)
    extension = os.path.splitext(safe_name)[1].lower()
    unique_name = f"{uuid.uuid4().hex}{extension}"
    file_path = os.path.join(upload_folder, unique_name)
    file_storage.stream.seek(0)
    file_storage.save(file_path)

    attachment = PurchaseRequestAttachment(
        request=purchase_request,
        filename=unique_name,
        original_name=safe_name,
        file_size=file_size or 0,
        uploaded_by=_current_actor(),
    )
    db.session.add(attachment)
    return True, None, attachment


@bp.route("/")
def purchasing_home():
    raw_status_filter = request.args.get("status")
    if raw_status_filter is None:
        status_filter = "open"
    else:
        status_filter = (raw_status_filter or "").strip().lower()
    valid_statuses = set(PurchaseRequest.status_values())
    query = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc())

    if status_filter == "open":
        query = query.filter(~PurchaseRequest.status.in_(CLOSED_STATUSES))
    elif "," in status_filter:
        requested_statuses = [value.strip() for value in status_filter.split(",") if value.strip()]
        if requested_statuses and all(status in valid_statuses for status in requested_statuses):
            query = query.filter(PurchaseRequest.status.in_(requested_statuses))
        elif status_filter:
            flash("Unknown status filter applied. Showing all requests.", "warning")
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

    available_columns = get_purchase_request_column_defs()
    allowed_keys = {column["key"] for column in available_columns}
    column_labels = {column["key"]: column["label"] for column in available_columns}
    default_columns = [
        key for key in PURCHASING_SHORTAGE_DEFAULT_COLUMNS if key in allowed_keys
    ]
    if not default_columns:
        default_columns = list(allowed_keys)

    visible_columns = _coerce_user_column_pref(
        _current_user_shortage_pref(),
        allowed_keys=allowed_keys,
        default_keys=default_columns,
    )

    return_to = request.full_path
    if return_to.endswith("?"):
        return_to = return_to[:-1]

    return render_template(
        "purchasing/home.html",
        requests=requests,
        status_filter=status_filter,
        status_choices=PurchaseRequest.STATUS_CHOICES,
        status_counts=status_counts,
        open_count=open_count,
        status_labels=dict(PurchaseRequest.STATUS_CHOICES),
        available_columns=available_columns,
        visible_columns=visible_columns,
        column_labels=column_labels,
        format_purchase_request_value=_format_purchase_request_value,
        purchase_request_attr=_purchase_request_attr,
        return_to=return_to,
    )


@bp.route("/shortages/columns", methods=["POST"])
def save_shortage_columns():
    available_columns = get_purchase_request_column_defs()
    allowed_keys = {column["key"] for column in available_columns}
    action = (request.form.get("action") or "save").strip().lower()

    if action == "reset":
        current_user.purchasing_shortage_columns = None
        db.session.commit()
        flash("Shortage column preferences reset to defaults.", "success")
        return redirect(
            _shortage_columns_return_target(url_for("purchasing.purchasing_home"))
        )

    selected: list[str] = []
    seen: set[str] = set()
    for key in request.form.getlist("columns"):
        if key in allowed_keys and key not in seen:
            selected.append(key)
            seen.add(key)

    if not selected:
        current_user.purchasing_shortage_columns = None
        db.session.commit()
        flash("No valid columns selected. Using defaults.", "warning")
    else:
        current_user.purchasing_shortage_columns = selected
        db.session.commit()
        flash("Shortage columns saved.", "success")

    return redirect(
        _shortage_columns_return_target(url_for("purchasing.purchasing_home"))
    )


@bp.route("/new", methods=["GET", "POST"])
@_require_purchasing_edit
def new_request():
    default_requestor = current_user.username if current_user.is_authenticated else ""
    form_data = purchase_request_form_defaults(default_requestor)

    if request.method == "POST":
        purchase_request, errors, form_data = build_purchase_request_from_form(
            request.form, default_requestor=default_requestor
        )
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            try:
                PurchaseRequest.commit_with_sequence_retry(purchase_request)
            except Exception:
                current_app.logger.exception(
                    "Failed to create purchase request from purchasing form"
                )
                raise
            flash("Item shortage logged for purchasing review.", "success")
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

    sku = purchase_request.item_number or _extract_sku_from_title(purchase_request.title)
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
    allowed_extensions = sorted(
        current_app.config.get("PURCHASING_ATTACHMENT_ALLOWED_EXTENSIONS", set())
    )
    return render_template(
        "purchasing/detail.html",
        purchase_request=purchase_request,
        status_choices=PurchaseRequest.STATUS_CHOICES,
        status_labels=dict(PurchaseRequest.STATUS_CHOICES),
        receive_url=receive_url,
        allowed_extensions=allowed_extensions,
    )


@bp.route("/<int:request_id>/delete/confirm")
def confirm_delete_request(request_id: int):
    guard = _require_superuser_delete()
    if guard is not None:
        return guard

    purchase_request = PurchaseRequest.query.get_or_404(request_id)
    return render_template(
        "purchasing/delete_confirm.html",
        purchase_request=purchase_request,
        csrf_token=_delete_request_csrf_token(),
    )


@bp.route("/<int:request_id>/delete", methods=["POST"])
def delete_request(request_id: int):
    guard = _require_superuser_delete()
    if guard is not None:
        return guard

    purchase_request = PurchaseRequest.query.get_or_404(request_id)
    token = request.form.get("csrf_token")
    if not _delete_request_csrf_valid(token):
        flash("Invalid delete request. Please try again.", "danger")
        return redirect(
            url_for("purchasing.confirm_delete_request", request_id=request_id)
        )

    delete_reason = (request.form.get("delete_reason") or "").strip() or None
    audit_entry = PurchaseRequestDeleteAudit(
        purchase_request_id=purchase_request.id,
        title=purchase_request.title,
        item_number=purchase_request.item_number,
        requested_by=purchase_request.requested_by,
        attachment_count=len(purchase_request.attachments),
        deleted_by_user_id=_current_user_id(),
        deleted_by_username=_current_username(),
        delete_reason=delete_reason,
    )
    upload_folder = current_app.config.get("PURCHASING_ATTACHMENT_UPLOAD_FOLDER")
    failed_files: list[str] = []
    if upload_folder:
        for attachment in purchase_request.attachments:
            file_path = os.path.join(upload_folder, attachment.filename)
            try:
                os.remove(file_path)
            except FileNotFoundError:
                continue
            except OSError:
                failed_files.append(attachment.original_name or attachment.filename)

    db.session.add(audit_entry)
    db.session.delete(purchase_request)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to delete purchase request %s", purchase_request.id
        )
        flash("Unable to delete the item shortage. Please try again.", "danger")
        return redirect(url_for("purchasing.view_request", request_id=request_id))

    if failed_files:
        flash(
            "Item shortage deleted, but some attachment files could not be removed.",
            "warning",
        )

    flash("Item shortage permanently deleted.", "success")
    return redirect(url_for("purchasing.purchasing_home", status="open"))


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

    shipped_date_value, shipped_date_error = _parse_date(
        request.form.get("shipped_from_supplier_date", ""),
        field_label="the shipped from supplier date",
    )
    if shipped_date_error:
        errors.append(shipped_date_error)
    else:
        purchase_request.shipped_from_supplier_date = shipped_date_value

    if errors:
        for message in errors:
            flash(message, "error")
        db.session.rollback()
    else:
        db.session.commit()
        flash("Item shortage updated.", "success")

    return redirect(url_for("purchasing.view_request", request_id=request_id))


@bp.route("/<int:request_id>/attachments", methods=["POST"])
@_require_purchasing_edit
def upload_attachment(request_id: int):
    purchase_request = PurchaseRequest.query.get_or_404(request_id)
    file_storage = request.files.get("attachment")

    success, message, _attachment = _save_purchase_attachment(
        purchase_request, file_storage
    )
    if not success:
        flash(message or "Attachment not uploaded.", "error")
        return redirect(url_for("purchasing.view_request", request_id=request_id))

    db.session.commit()
    flash("Attachment uploaded.", "success")
    return redirect(url_for("purchasing.view_request", request_id=request_id))


@bp.route(
    "/<int:request_id>/attachments/<int:attachment_id>/download",
    methods=["GET"],
)
def download_attachment(request_id: int, attachment_id: int):
    attachment = PurchaseRequestAttachment.query.filter_by(
        id=attachment_id, request_id=request_id
    ).first()
    if attachment is None:
        abort(404)

    upload_folder = current_app.config.get("PURCHASING_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        abort(404)

    return send_from_directory(
        upload_folder,
        attachment.filename,
        as_attachment=True,
        download_name=attachment.original_name,
    )


@bp.route(
    "/<int:request_id>/attachments/<int:attachment_id>/delete",
    methods=["POST"],
)
@_require_purchasing_edit
def delete_attachment(request_id: int, attachment_id: int):
    attachment = PurchaseRequestAttachment.query.filter_by(
        id=attachment_id, request_id=request_id
    ).first()
    if attachment is None:
        abort(404)

    upload_folder = current_app.config.get("PURCHASING_ATTACHMENT_UPLOAD_FOLDER")
    if upload_folder:
        file_path = os.path.join(upload_folder, attachment.filename)
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        except OSError:
            flash(
                "Attachment removed from record, but the file could not be deleted.",
                "warning",
            )

    db.session.delete(attachment)
    db.session.commit()
    flash("Attachment removed.", "success")
    return redirect(url_for("purchasing.view_request", request_id=request_id))
