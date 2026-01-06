import base64
import csv
import io
import os
import secrets
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping, Optional, Union

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, load_only

from invapp.auth import blueprint_page_guard
from invapp.login import current_user
from invapp.permissions import resolve_edit_roles
from invapp.security import require_any_role, require_roles
from invapp.models import (
    Batch,
    BillOfMaterial,
    BillOfMaterialComponent,
    Item,
    ItemAttachment,
    Location,
    Movement,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    PurchaseRequest,
    Reservation,
    RoutingStepConsumption,
    db,
)
from werkzeug.utils import secure_filename

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

bp.before_request(blueprint_page_guard("inventory"))


UNASSIGNED_LOCATION_CODE = "UNASSIGNED"
PLACEHOLDER_CREATION_MAX_RETRIES = 5
PLACEHOLDER_CREATION_INITIAL_BACKOFF = 0.05


def _ensure_placeholder_location(loc_map: dict[str, "Location"]) -> "Location":
    """Return the shared placeholder location, creating it if necessary.

    When multiple workers attempt to insert the placeholder concurrently the
    losing workers may not see the new row immediately because the winning
    transaction has not committed yet. We therefore retry a few times with a
    short backoff before surfacing the original integrity error.
    """

    placeholder = loc_map.get(UNASSIGNED_LOCATION_CODE)
    if placeholder:
        return placeholder

    attempts = 0
    backoff = PLACEHOLDER_CREATION_INITIAL_BACKOFF

    while True:
        placeholder = Location(
            code=UNASSIGNED_LOCATION_CODE,
            description="Unassigned staging location",
        )
        db.session.add(placeholder)
        try:
            db.session.flush()
        except IntegrityError as exc:  # pragma: no cover - exercised in tests
            db.session.rollback()
            existing = Location.query.filter_by(
                code=UNASSIGNED_LOCATION_CODE
            ).one_or_none()
            if existing:
                loc_map[UNASSIGNED_LOCATION_CODE] = existing
                return existing

            attempts += 1
            if attempts > PLACEHOLDER_CREATION_MAX_RETRIES:
                raise exc

            time.sleep(backoff)
            backoff *= 2
            continue

        loc_map[UNASSIGNED_LOCATION_CODE] = placeholder
        return placeholder


AUTO_SKU_START = 100000


def _next_auto_sku_value() -> int:
    """Return the next auto-generated SKU as an integer.

    Ensures SKUs are at least six digits by starting at ``AUTO_SKU_START`` when
    no numeric SKUs exist or when legacy numeric SKUs are below that threshold.
    """

    max_sku_val = db.session.query(db.func.max(Item.sku.cast(db.Integer))).scalar()
    highest_numeric = int(max_sku_val) if max_sku_val else 0
    baseline = AUTO_SKU_START - 1
    return max(highest_numeric, baseline) + 1


def _next_auto_sku() -> str:
    """Return the next auto-generated SKU as a string."""

    return str(_next_auto_sku_value())


ITEM_IMPORT_FIELDS = [
    {"field": "sku", "label": "SKU", "required": False},
    {"field": "name", "label": "Name", "required": True},
    {"field": "type", "label": "Type", "required": False},
    {"field": "unit", "label": "Unit", "required": False},
    {"field": "description", "label": "Description", "required": False},
    {"field": "min_stock", "label": "Minimum Stock", "required": False},
    {"field": "notes", "label": "Notes", "required": False},
    {"field": "list_price", "label": "List Price", "required": False},
    {"field": "last_unit_cost", "label": "Last Unit Cost", "required": False},
    {"field": "item_class", "label": "Item Class", "required": False},
]


LOCATION_IMPORT_FIELDS = [
    {"field": "code", "label": "Location Code", "required": True},
    {"field": "description", "label": "Description", "required": False},
]

STOCK_IMPORT_FIELDS = [
    {"field": "sku", "label": "Item SKU", "required": True},
    {"field": "location_code", "label": "Location Code", "required": False},
    {"field": "quantity", "label": "Quantity", "required": True},
    {"field": "lot_number", "label": "Lot Number", "required": False},
    {"field": "person", "label": "Person", "required": False},
    {"field": "reference", "label": "Reference", "required": False},
]


IMPORT_STORAGE_ROOT = os.path.join(tempfile.gettempdir(), "invapp_imports")
IMPORT_FILE_TTL_SECONDS = 3600  # one hour


def _allowed_item_attachment(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    allowed = current_app.config.get("ITEM_ATTACHMENT_ALLOWED_EXTENSIONS", set())
    return extension in allowed


def _save_item_attachment(item: Item, file_storage):
    if not file_storage or not file_storage.filename:
        return False, None

    filename = file_storage.filename
    if not _allowed_item_attachment(filename):
        allowed = current_app.config.get("ITEM_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        allowed_list = ", ".join(sorted(allowed)) if allowed else "(none)"
        return (
            False,
            f"Attachment not saved. Allowed file types: {allowed_list}",
        )

    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = f"attachment_{uuid.uuid4().hex}"

    upload_folder = current_app.config.get("ITEM_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        return False, "Attachment upload folder is not configured."

    os.makedirs(upload_folder, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = os.path.join(upload_folder, unique_name)
    file_storage.save(file_path)

    db.session.add(
        ItemAttachment(
            item=item,
            filename=unique_name,
            original_name=safe_name,
        )
    )
    return True, None


def _get_import_storage_dir(namespace):
    path = os.path.join(IMPORT_STORAGE_ROOT, namespace)
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_import_storage(namespace, now=None):
    """Remove stale cached CSV files from previous imports for a namespace."""

    storage_dir = _get_import_storage_dir(namespace)
    current_time = now or time.time()
    try:
        for name in os.listdir(storage_dir):
            path = os.path.join(storage_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                if current_time - os.path.getmtime(path) > IMPORT_FILE_TTL_SECONDS:

                    os.remove(path)
            except OSError:
                continue
    except FileNotFoundError:
        # Directory was removed between ensure + listdir; recreate lazily later
        pass



def _store_import_csv(namespace, csv_text, token=None):
    """Persist CSV text for an import namespace and return its token."""

    _cleanup_import_storage(namespace)
    if token and any(ch in token for ch in ("/", "\\")):
        token = None
    if not token:
        token = secrets.token_urlsafe(16)

    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.csv")

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(csv_text)
    except OSError:
        if token:
            return token
        return None
    return token



def _load_import_csv(namespace, token):
    if not token or any(ch in token for ch in ("/", "\\")):
        return None
    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.csv")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None



def _remove_import_csv(namespace, token):
    if not token or any(ch in token for ch in ("/", "\\")):
        return
    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.csv")

    try:
        os.remove(path)
    except OSError:
        pass


def _parse_decimal(value):
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        value = str(value)
    value = value.strip()
    if not value:
        return None
    try:
        return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


def _prepare_import_mapping_context(
    csv_text, fields, namespace, selected_mappings=None, token=None
):

    stream = io.StringIO(csv_text)
    reader = csv.reader(stream)
    try:
        headers = next(reader)
    except StopIteration:
        headers = []

    sample_rows = []
    if headers:
        for _ in range(5):
            try:
                sample_rows.append(next(reader))
            except StopIteration:
                break


    import_token = _store_import_csv(namespace, csv_text, token=token)


    return {
        "headers": headers,
        "sample_rows": sample_rows,
        "import_token": import_token,

        "fields": fields,
        "selected_mappings": selected_mappings or {},
    }


def _prepare_item_import_mapping_context(csv_text, selected_mappings=None, token=None):
    return _prepare_import_mapping_context(
        csv_text, ITEM_IMPORT_FIELDS, "items", selected_mappings=selected_mappings, token=token
    )


def _prepare_location_import_mapping_context(
    csv_text, selected_mappings=None, token=None
):
    return _prepare_import_mapping_context(
        csv_text,
        LOCATION_IMPORT_FIELDS,
        "locations",
        selected_mappings=selected_mappings,
        token=token,
    )


def _prepare_stock_import_mapping_context(csv_text, selected_mappings=None, token=None):
    return _prepare_import_mapping_context(
        csv_text,
        STOCK_IMPORT_FIELDS,
        "stock",
        selected_mappings=selected_mappings,
        token=token,
    )


############################
# HOME
############################
@bp.route("/")
def inventory_home():
    # Summaries for on-hand inventory and reservations
    movement_totals = (
        db.session.query(
            Movement.item_id,
            func.coalesce(func.sum(Movement.quantity), 0).label("on_hand"),
        )
        .group_by(Movement.item_id)
        .all()
    )
    on_hand_map = {item_id: Decimal(total or 0) for item_id, total in movement_totals}

    reservation_totals = (
        db.session.query(
            Reservation.item_id,
            func.coalesce(func.sum(Reservation.quantity), 0).label("reserved"),
        )
        .join(OrderLine)
        .join(Order)
        .filter(Order.status.in_(OrderStatus.RESERVABLE_STATES))
        .group_by(Reservation.item_id)
        .all()
    )
    reserved_map = {item_id: Decimal(total or 0) for item_id, total in reservation_totals}

    items = (
        Item.query.options(load_only(Item.id, Item.sku, Item.name, Item.min_stock))
        .order_by(Item.sku)
        .all()
    )
    items_by_id = {item.id: item for item in items}

    usage_cutoff = datetime.utcnow() - timedelta(days=30)
    usage_totals = (
        db.session.query(
            Movement.item_id,
            func.sum(Movement.quantity).label("usage"),
        )
        .filter(
            Movement.movement_type == "ISSUE",
            Movement.quantity < 0,
            Movement.date >= usage_cutoff,
        )
        .group_by(Movement.item_id)
        .all()
    )
    usage_map = {
        item_id: int(-total)
        for item_id, total in usage_totals
        if total and total < 0
    }

    # Determine inventory warning zones
    low_stock_items = []
    near_stock_items = []
    for item in items:
        min_stock_int = int(item.min_stock or 0)
        if min_stock_int <= 0:
            continue
        min_stock = Decimal(min_stock_int)
        total_on_hand = on_hand_map.get(item.id, Decimal(0))
        coverage = (total_on_hand / min_stock) if min_stock else None
        entry = {
            "item": item,
            "on_hand": total_on_hand,
            "min_stock": min_stock_int,
            "coverage": coverage,
        }
        if total_on_hand < (min_stock * Decimal("1.05")):
            low_stock_items.append(entry)
        elif total_on_hand < (min_stock * Decimal("1.25")):
            near_stock_items.append(entry)

    def _coverage_sort_key(entry):
        coverage = entry.get("coverage")
        return float(coverage) if coverage is not None else float("inf")

    low_stock_items.sort(key=_coverage_sort_key)
    near_stock_items.sort(key=_coverage_sort_key)

    # Aggregate items causing material shortages for orders
    waiting_orders = (
        Order.query.options(
            joinedload(Order.order_lines)
            .joinedload(OrderLine.components)
            .joinedload(OrderComponent.component_item)
        )
        .filter(Order.status == OrderStatus.WAITING_MATERIAL)
        .order_by(Order.order_number)
        .all()
    )

    required_by_item = defaultdict(Decimal)
    order_refs = defaultdict(list)
    for order in waiting_orders:
        for line in order.order_lines:
            line_quantity = Decimal(line.quantity or 0)
            if line_quantity <= 0:
                continue
            for component in line.components:
                component_quantity = Decimal(component.quantity or 0)
                if component_quantity <= 0:
                    continue
                total_required = component_quantity * line_quantity
                required_by_item[component.component_item_id] += total_required
                order_refs[component.component_item_id].append(
                    {
                        "order_number": order.order_number,
                        "order_id": order.id,
                        "required": total_required,
                    }
                )

    for entries in order_refs.values():
        entries.sort(key=lambda value: value["order_number"])

    waiting_items = []
    for item_id, required_total in required_by_item.items():
        available = on_hand_map.get(item_id, Decimal(0)) - reserved_map.get(
            item_id, Decimal(0)
        )
        if available < 0:
            available = Decimal(0)
        shortage = required_total - available
        if shortage <= 0:
            continue
        waiting_items.append(
            {
                "item": items_by_id.get(item_id),
                "total_required": required_total,
                "available": available,
                "shortage": shortage,
                "orders": order_refs[item_id],
            }
        )

    waiting_items.sort(key=lambda entry: entry["shortage"], reverse=True)

    def _build_chart_entries(source_map):
        entries = []
        for item_id, raw_value in source_map.items():
            value = float(raw_value)
            if value <= 0:
                continue
            item = items_by_id.get(item_id)
            if not item:
                continue
            entries.append(
                {"sku": item.sku, "name": item.name, "value": value}
            )
        entries.sort(key=lambda entry: entry["value"], reverse=True)
        return entries[:10]

    shortage_entries = []
    for record in waiting_items:
        shortage_value = int(record.get("shortage", 0) or 0)
        if shortage_value <= 0:
            continue
        item = record.get("item")
        sku = item.sku if item else "Unknown"
        name = item.name if item else "Item awaiting setup"
        shortage_entries.append(
            {"sku": sku, "name": name, "value": shortage_value}
        )

    chart_datasets = {
        "usage": {
            "title": "Usage (Last 30 Days)",
            "description": (
                "Top components issued in the past 30 days (since "
                f"{usage_cutoff.strftime('%Y-%m-%d')})."
            ),
            "entries": _build_chart_entries(usage_map),
        },
        "on_hand": {
            "title": "Inventory On Hand",
            "description": "Items with the highest current on-hand balances.",
            "entries": _build_chart_entries(on_hand_map),
        },
        "reserved": {
            "title": "Reserved for Active Orders",
            "description": (
                "Components reserved against open and scheduled work orders."
            ),
            "entries": _build_chart_entries(reserved_map),
        },
        "shortages": {
            "title": "Waiting on Material",
            "description": (
                "Outstanding shortages preventing orders from progressing."
            ),
            "entries": shortage_entries[:10],
        },
    }

    preferred_keys = ("usage", "on_hand", "reserved", "shortages")
    default_chart_key = next(
        (key for key in preferred_keys if chart_datasets[key]["entries"]),
        preferred_keys[0],
    )

    return render_template(
        "inventory/home.html",
        waiting_items=waiting_items,
        low_stock_items=low_stock_items,
        near_stock_items=near_stock_items,
        inventory_chart_data=chart_datasets,
        inventory_chart_default=default_chart_key,
    )


def _create_purchase_request_for_item(
    item: Item,
    description: str,
    success_message: str,
    quantity: Optional[int] = None,
):
    quantity_value = None
    if quantity is not None:
        try:
            quantity_int = int(quantity)
        except (TypeError, ValueError):
            quantity_int = 0
        if quantity_int > 0:
            quantity_value = Decimal(quantity_int)

    requester = (
        current_user.username
        if current_user.is_authenticated and getattr(current_user, "username", None)
        else "inventory"
    )

    title = f"{item.sku} – {item.name}"
    existing_request = (
        PurchaseRequest.query.filter(
            PurchaseRequest.title == title,
            ~PurchaseRequest.status.in_(
                (PurchaseRequest.STATUS_RECEIVED, PurchaseRequest.STATUS_CANCELLED)
            ),
        )
        .order_by(PurchaseRequest.created_at.desc())
        .first()
    )
    if existing_request:
        flash(
            "An open item shortage already exists for this item. Redirecting to the existing record.",
            "info",
        )
        return redirect(url_for("purchasing.view_request", request_id=existing_request.id))

    purchase_request = PurchaseRequest(
        title=title,
        description=description,
        quantity=quantity_value,
        unit=item.unit,
        requested_by=requester,
        notes="Update with supplier details and ordering information as needed.",
    )
    try:
        PurchaseRequest.commit_with_sequence_retry(purchase_request)
    except Exception:
        current_app.logger.exception(
            "Failed to create purchase request from dashboard shortcut"
        )
        raise

    flash(success_message, "success")
    return redirect(url_for("purchasing.view_request", request_id=purchase_request.id))


@bp.route("/low-stock/<int:item_id>/purchase-request", methods=["POST"])
def create_purchase_request_from_low_stock(item_id: int):
    """Convert a dashboard low stock alert into a purchase request."""

    edit_roles = resolve_edit_roles(
        "purchasing", default_roles=("editor", "admin", "purchasing")
    )
    guard = require_any_role(edit_roles)

    @guard
    def _create_request():
        item = Item.query.get_or_404(item_id)

        total_on_hand = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item.id)
            .scalar()
        ) or 0
        total_on_hand = int(total_on_hand)
        min_stock = int(item.min_stock or 0)
        recommended_quantity = max(min_stock - total_on_hand, 0)

        description = (
            "Generated from the low stock alert on the inventory dashboard. "
            f"On-hand balance: {total_on_hand}. Minimum stock: {min_stock}."
        )

        return _create_purchase_request_for_item(
            item,
            description,
            "Item shortage created from low stock alert.",
            recommended_quantity,
        )

    return _create_request()


@bp.route("/waiting/<int:item_id>/purchase-request", methods=["POST"])
def create_purchase_request_from_waiting(item_id: int):
    """Create a purchase request for items delaying orders."""

    edit_roles = resolve_edit_roles(
        "purchasing", default_roles=("editor", "admin", "purchasing")
    )
    guard = require_any_role(edit_roles)

    @guard
    def _create_request():
        item = Item.query.get_or_404(item_id)

        total_on_hand = Decimal(
            (
                db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
                .filter(Movement.item_id == item.id)
                .scalar()
            )
            or 0
        )

        reserved_total = Decimal(
            (
                db.session.query(func.coalesce(func.sum(Reservation.quantity), 0))
                .join(OrderLine)
                .join(Order)
                .filter(
                    Reservation.item_id == item.id,
                    Order.status.in_(OrderStatus.RESERVABLE_STATES),
                )
                .scalar()
            )
            or 0
        )
        available_after_reservations = max(total_on_hand - reserved_total, Decimal(0))

        waiting_orders = (
            Order.query.options(
                joinedload(Order.order_lines)
                .joinedload(OrderLine.components)
                .joinedload(OrderComponent.component_item)
            )
            .filter(Order.status == OrderStatus.WAITING_MATERIAL)
            .all()
        )

        total_required = Decimal(0)
        for order in waiting_orders:
            for line in order.order_lines:
                line_quantity = Decimal(line.quantity or 0)
                if line_quantity <= 0:
                    continue
                for component in line.components:
                    if component.component_item_id != item.id:
                        continue
                    component_quantity = Decimal(component.quantity or 0)
                    if component_quantity <= 0:
                        continue
                    total_required += component_quantity * line_quantity

        if total_required <= 0:
            flash(
                "No outstanding waiting material requirements were found for this item.",
                "info",
            )
            return redirect(url_for("inventory.inventory_home"))

        shortage = total_required - available_after_reservations
        if shortage <= 0:
            flash(
                "This item no longer has a shortage after accounting for current availability.",
                "info",
            )
            return redirect(url_for("inventory.inventory_home"))

        description = (
            "Generated from the Waiting on Material list on the inventory dashboard. "
            f"Required for waiting orders: {float(total_required)}. "
            f"Available after reservations: {float(available_after_reservations)}. "
            f"Calculated shortage: {float(shortage)}."
        )

        return _create_purchase_request_for_item(
            item,
            description,
            "Item shortage created for waiting material shortage.",
            shortage,
        )

    return _create_request()


@bp.route("/scan")
def scan_inventory():
    lookup_template = url_for("inventory.lookup_item_api", sku="__SKU__")
    return render_template(
        "inventory/scan.html",
        lookup_template=lookup_template,
    )


@bp.get("/api/items/search")
def search_items_api():
    """Search items by SKU or name for quick lookups."""

    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query is required"}), 400

    like_pattern = f"%{query}%"
    matches = (
        Item.query.options(load_only(Item.sku, Item.name))
        .filter(or_(Item.sku.ilike(like_pattern), Item.name.ilike(like_pattern)))
        .order_by(Item.sku)
        .limit(20)
        .all()
    )

    return jsonify(
        {
            "results": [
                {
                    "sku": item.sku,
                    "name": item.name,
                }
                for item in matches
            ]
        }
    )


@bp.route("/api/items/<sku>")
def lookup_item_api(sku):
    sku = sku.strip()
    if not sku:
        return jsonify({"error": "SKU is required"}), 400

    item = (
        Item.query.options(
            load_only(
                Item.sku,
                Item.name,
                Item.description,
                Item.unit,
                Item.list_price,
                Item.last_unit_cost,
                Item.item_class,
                Item.type,
                Item.default_location_id,
            ),
            joinedload(Item.default_location).load_only(
                Location.id, Location.code, Location.description
            ),
        )
        .filter(func.lower(Item.sku) == sku.lower())
        .first()
    )

    if not item:
        return jsonify({"error": "Item not found"}), 404

    return jsonify(
        {
            "sku": item.sku,
            "name": item.name,
            "description": item.description or "",
            "unit": item.unit or "",
            "type": item.type or "",
            "list_price": _decimal_to_string(item.list_price),
            "last_unit_cost": _decimal_to_string(item.last_unit_cost),
            "item_class": item.item_class or "",
            "default_location": (
                {
                    "id": item.default_location.id,
                    "code": item.default_location.code,
                    "description": item.default_location.description,
                }
                if item.default_location
                else None
            ),
        }
    )

############################
# CYCLE COUNT ROUTES
############################
@bp.route("/cycle-count", methods=["GET", "POST"])
def cycle_count_home():
    items = Item.query.options(load_only(Item.id, Item.sku, Item.name)).all()
    locations = Location.query.options(load_only(Location.id, Location.code)).all()
    batches = Batch.query.options(load_only(Batch.id, Batch.lot_number)).all()

    if request.method == "POST":
        sku = request.form["sku"].strip()
        batch_id = int(request.form["batch_id"])
        location_id = int(request.form["location_id"])
        counted_qty = int(request.form["counted_qty"])
        person = request.form["person"].strip()
        reference = request.form.get("reference", "Cycle Count")

        item = Item.query.filter_by(sku=sku).first()
        batch = Batch.query.get(batch_id)
        if not item or not batch:
            flash("Invalid SKU or Batch selected.", "danger")
            return redirect(url_for("inventory.cycle_count_home"))

        # Current (book) balance
        book_qty = (
            db.session.query(func.sum(Movement.quantity))
            .filter_by(item_id=item.id, batch_id=batch_id, location_id=location_id)
            .scalar()
        ) or 0

        diff = counted_qty - book_qty

        if diff == 0:
            movement_type = "CYCLE_COUNT_CONFIRM"
            qty_to_record = 0
        else:
            movement_type = "CYCLE_COUNT_ADJUSTMENT"
            qty_to_record = diff

        mv = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=location_id,
            quantity=qty_to_record,
            movement_type=movement_type,
            person=person,
            reference=f"{reference} (Book={book_qty}, Counted={counted_qty})",
        )
        db.session.add(mv)
        db.session.commit()

        flash(
            f"Cycle Count logged for {sku}: Book={book_qty}, Counted={counted_qty}, Difference={diff}",
            "success",
        )
        return redirect(url_for("inventory.cycle_count_home"))

    # Recent cycle counts
    records = (
        Movement.query.options(
            joinedload(Movement.item).load_only(Item.sku, Item.name),
            joinedload(Movement.location).load_only(Location.code),
            joinedload(Movement.batch).load_only(Batch.lot_number),
        )
        .filter(
            Movement.movement_type.in_(
                ["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]
            )
        )
        .order_by(Movement.date.desc())
        .limit(50)
        .all()
    )

    return render_template(
        "inventory/cycle_count.html",
        items=items,
        locations=locations,
        batches=batches,
        records=records,
    )

@bp.route("/cycle-count/export")
def export_cycle_counts():
    records = (
        Movement.query.options(
            joinedload(Movement.item).load_only(Item.sku, Item.name),
            joinedload(Movement.location).load_only(Location.code),
            joinedload(Movement.batch).load_only(Batch.lot_number),
        )
        .filter(
            Movement.movement_type.in_(
                ["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]
            )
        )
        .order_by(Movement.date.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date",
        "sku",
        "item",
        "lot_number",
        "location",
        "book_vs_counted",
        "person",
        "movement_type"
    ])

    for rec in records:
        sku = rec.item.sku if rec.item else "???"
        item_name = rec.item.name if rec.item else "Unknown"
        lot = rec.batch.lot_number if rec.batch else "-"
        loc = rec.location.code if rec.location else "-"
        writer.writerow([
            rec.date.strftime("%Y-%m-%d %H:%M"),
            sku,
            item_name,
            lot,
            loc,
            rec.reference,
            rec.person or "-",
            rec.movement_type,
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=cycle_counts.csv"
    return response



############################
# ITEM ROUTES
############################
@bp.route("/items")
def list_items():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    selected_type = request.args.get("type", type=str)
    sort_param = request.args.get("sort", "sku")
    order = request.args.get("order", "asc")
    search = request.args.get("search", "")

    on_hand_subquery = (
        db.session.query(
            Movement.item_id.label("item_id"),
            func.sum(Movement.quantity).label("on_hand"),
        )
        .group_by(Movement.item_id)
        .subquery()
    )

    on_hand_coalesced = func.coalesce(on_hand_subquery.c.on_hand, 0)

    query = Item.query.outerjoin(on_hand_subquery, Item.id == on_hand_subquery.c.item_id)
    if selected_type:
        query = query.filter(Item.type == selected_type)
    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(Item.sku.ilike(like_pattern), Item.name.ilike(like_pattern))
        )

    sort_columns = {
        "sku": Item.sku,
        "name": Item.name,
        "type": Item.type,
        "unit": Item.unit,
        "min_stock": Item.min_stock,
        "list_price": Item.list_price,
        "last_unit_cost": Item.last_unit_cost,
        "item_class": Item.item_class,
        "on_hand": on_hand_coalesced,
    }

    sort_expression = sort_columns.get(sort_param, Item.sku)
    if order == "desc":
        sort_expression = sort_expression.desc()
    else:
        order = "asc"
        sort_expression = sort_expression.asc()

    query = query.order_by(sort_expression)

    pagination = query.paginate(page=page, per_page=size, error_out=False)

    on_hand_totals = {}
    if pagination.items:
        item_ids = [item.id for item in pagination.items]
        quantity_totals = (
            db.session.query(
                Movement.item_id, func.sum(Movement.quantity).label("on_hand")
            )
            .filter(Movement.item_id.in_(item_ids))
            .group_by(Movement.item_id)
            .all()
        )
        on_hand_totals = {row.item_id: row.on_hand for row in quantity_totals}

    types_query = (
        db.session.query(Item.type)
        .filter(Item.type.isnot(None))
        .filter(Item.type != "")
        .distinct()
        .order_by(Item.type.asc())
    )
    available_types = [row[0] for row in types_query]
    delete_all_prompt = session.pop("delete_all_prompt", None)
    return render_template(
        "inventory/list_items.html",
        items=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
        available_types=available_types,
        selected_type=selected_type,
        sort=sort_param,
        order=order,
        search=search,
        delete_all_prompt=delete_all_prompt,
        on_hand_totals=on_hand_totals,
    )


@bp.route("/item/<int:item_id>")
def view_item(item_id):
    item = Item.query.options(joinedload(Item.attachments)).get_or_404(item_id)

    location_totals = (
        db.session.query(
            Movement.location_id, func.sum(Movement.quantity).label("on_hand")
        )
        .filter(Movement.item_id == item.id)
        .group_by(Movement.location_id)
        .having(func.sum(Movement.quantity) != 0)
        .all()
    )

    location_ids = [location_id for location_id, _ in location_totals if location_id is not None]
    locations = (
        {
            loc.id: loc
            for loc in Location.query.filter(Location.id.in_(location_ids)).all()
        }
        if location_ids
        else {}
    )

    location_balances = []
    for location_id, on_hand in location_totals:
        location = locations.get(location_id)
        if not location:
            continue
        location_balances.append({"location": location, "on_hand": int(on_hand)})

    location_balances.sort(key=lambda entry: entry["location"].code or "")
    total_on_hand = sum(entry["on_hand"] for entry in location_balances)

    edit_roles = resolve_edit_roles(
        "inventory", default_roles=("editor", "admin", "inventory")
    )
    can_edit = current_user.is_authenticated and current_user.has_any_role(edit_roles)

    return render_template(
        "inventory/view_item.html",
        item=item,
        location_balances=location_balances,
        total_on_hand=total_on_hand,
        can_edit=can_edit,
    )


@bp.route("/item/add", methods=["GET", "POST"])
def add_item():
    locations = Location.query.order_by(Location.code.asc()).all()

    if request.method == "POST":
        next_sku = _next_auto_sku()

        min_stock_raw = request.form.get("min_stock", 0)
        try:
            min_stock = int(min_stock_raw or 0)
        except (TypeError, ValueError):
            min_stock = 0

        notes_raw = request.form.get("notes")
        notes = notes_raw.strip() if notes_raw is not None else None
        notes_value = notes or None

        name = request.form.get("name", "").strip()
        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "inventory/add_item.html", next_sku=next_sku, locations=locations
            )
        unit = request.form.get("unit", "ea") or "ea"
        description = request.form.get("description", "")
        item_type = request.form.get("type", "").strip() or None
        item_class = request.form.get("item_class", "").strip() or None

        attachment_saved = False

        default_location_id_raw = request.form.get("default_location_id")
        default_location_id = None
        if default_location_id_raw:
            try:
                default_location_id = int(default_location_id_raw)
            except (TypeError, ValueError):
                default_location_id = None

        default_location = (
            Location.query.get(default_location_id) if default_location_id else None
        )

        item = Item(
            sku=next_sku,
            name=name,
            type=item_type,
            unit=unit.strip() or "ea",
            description=description,
            min_stock=min_stock,
            notes=notes_value,
            list_price=_parse_decimal(request.form.get("list_price")),
            last_unit_cost=_parse_decimal(request.form.get("last_unit_cost")),
            item_class=item_class,
            default_location=default_location,
        )
        db.session.add(item)
        db.session.flush()

        attachment_file = request.files.get("attachment")
        saved, error_message = _save_item_attachment(item, attachment_file)
        if saved:
            attachment_saved = True
        elif error_message:
            flash(error_message, "danger")

        db.session.commit()
        note_msg = " (notes saved)" if notes_value else ""
        attachment_msg = " (attachment uploaded)" if attachment_saved else ""
        flash(
            f"Item added successfully with SKU {next_sku}{note_msg}{attachment_msg}",
            "success",
        )
        return redirect(url_for("inventory.list_items"))

    return render_template(
        "inventory/add_item.html", next_sku=_next_auto_sku(), locations=locations
    )


@bp.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
def edit_item(item_id):
    edit_roles = resolve_edit_roles(
        "inventory", default_roles=("editor", "admin", "inventory")
    )
    guard = require_any_role(edit_roles)

    @guard
    def _edit_item(item_id):
        item = Item.query.get_or_404(item_id)
        locations = Location.query.order_by(Location.code.asc()).all()

        if request.method == "POST":
            name_value = request.form.get("name", "").strip()
            if not name_value:
                flash("Name is required.", "danger")
                return render_template(
                    "inventory/edit_item.html", item=item, locations=locations
                )

            item.name = name_value
            item.type = request.form.get("type", "").strip() or None
            item.unit = request.form.get("unit", "ea").strip() or "ea"
            item.description = request.form.get("description", "").strip()

            min_stock_raw = request.form.get("min_stock", 0)
            try:
                item.min_stock = int(min_stock_raw or 0)
            except (TypeError, ValueError):
                item.min_stock = 0

            notes_raw = request.form.get("notes")
            notes = notes_raw.strip() if notes_raw is not None else None
            notes_value = notes or None
            item.notes = notes_value

            item.list_price = _parse_decimal(request.form.get("list_price"))
            item.last_unit_cost = _parse_decimal(request.form.get("last_unit_cost"))
            item.item_class = request.form.get("item_class", "").strip() or None

            default_location_id_raw = request.form.get("default_location_id")
            if default_location_id_raw:
                try:
                    parsed_id = int(default_location_id_raw)
                    location_choice = Location.query.get(parsed_id)
                    item.default_location_id = location_choice.id if location_choice else None
                except (TypeError, ValueError):
                    item.default_location_id = None
            else:
                item.default_location_id = None

            attachment_file = request.files.get("attachment")
            attachment_saved, error_message = _save_item_attachment(item, attachment_file)
            if not attachment_saved and error_message:
                flash(error_message, "danger")

            db.session.commit()
            if notes_raw is not None:
                if notes_value:
                    note_msg = " (notes saved)"
                else:
                    note_msg = " (notes cleared)"
            else:
                note_msg = ""
            attachment_msg = (
                " (attachment uploaded)" if attachment_file and attachment_saved else ""
            )
            flash(
                f"Item {item.sku} updated successfully{note_msg}{attachment_msg}",
                "success",
            )
            return redirect(url_for("inventory.list_items"))

        return render_template("inventory/edit_item.html", item=item, locations=locations)

    return _edit_item(item_id)


@bp.route(
    "/item/<int:item_id>/attachments/<int:attachment_id>/download",
    methods=["GET"],
)
def download_item_attachment(item_id, attachment_id):
    attachment = (
        ItemAttachment.query.filter_by(id=attachment_id, item_id=item_id)
        .first_or_404()
    )
    upload_folder = current_app.config.get("ITEM_ATTACHMENT_UPLOAD_FOLDER")
    if not upload_folder:
        abort(404)

    return send_from_directory(
        upload_folder,
        attachment.filename,
        as_attachment=True,
        download_name=attachment.original_name,
    )


@bp.route(
    "/item/<int:item_id>/attachments/<int:attachment_id>/delete",
    methods=["POST"],
)
@require_roles("admin")
def delete_item_attachment(item_id, attachment_id):
    item = Item.query.get_or_404(item_id)
    attachment = (
        ItemAttachment.query.filter_by(id=attachment_id, item_id=item.id)
        .first_or_404()
    )

    upload_folder = current_app.config.get("ITEM_ATTACHMENT_UPLOAD_FOLDER")
    if upload_folder:
        file_path = os.path.join(upload_folder, attachment.filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass

    db.session.delete(attachment)
    db.session.commit()
    flash("Attachment removed.", "success")
    return redirect(url_for("inventory.edit_item", item_id=item.id))


@bp.route("/item/<int:item_id>/delete", methods=["POST"])
@require_roles("admin")
def delete_item(item_id):
    item = Item.query.get_or_404(item_id)

    has_batches = Batch.query.filter_by(item_id=item.id).first() is not None
    has_movements = Movement.query.filter_by(item_id=item.id).first() is not None
    has_order_lines = OrderLine.query.filter_by(item_id=item.id).first() is not None
    has_components = (
        OrderComponent.query.filter_by(component_item_id=item.id).first() is not None
    )
    has_reservations = Reservation.query.filter_by(item_id=item.id).first() is not None

    if any([has_batches, has_movements, has_order_lines, has_components, has_reservations]):
        flash(
            "Cannot delete item because related stock, order, or reservation records exist.",
            "danger",
        )
        return redirect(url_for("inventory.edit_item", item_id=item.id))

    sku = item.sku
    db.session.delete(item)
    db.session.commit()
    flash(f"Item {sku} deleted successfully.", "success")
    return redirect(url_for("inventory.list_items"))


def _gather_item_dependency_info():
    dependency_columns = [
        ("stock movements", Movement.item_id),
        ("stock batches", Batch.item_id),
        ("order lines", OrderLine.item_id),
        ("order components", OrderComponent.component_item_id),
        ("reservations", Reservation.item_id),
        ("bills of material", BillOfMaterial.item_id),
        ("bill of material components", BillOfMaterialComponent.component_item_id),
    ]

    blocked_sources = []
    dependent_item_ids = set()

    for name, column in dependency_columns:
        query = db.session.query(column).filter(column.isnot(None))
        if query.limit(1).first():
            blocked_sources.append(name)
            dependent_item_ids.update(value for (value,) in query.distinct())

    return blocked_sources, dependent_item_ids


def _deletable_items_query(dependent_item_ids):
    query = Item.query
    if dependent_item_ids:
        query = query.filter(~Item.id.in_(dependent_item_ids))
    return query


def _delete_stock_records():
    consumptions_deleted = RoutingStepConsumption.query.delete(synchronize_session=False)
    movements_deleted = Movement.query.delete(synchronize_session=False)
    batches_deleted = Batch.query.delete(synchronize_session=False)
    db.session.commit()
    return consumptions_deleted, movements_deleted, batches_deleted


def _flash_stock_deletion_summary(consumptions_deleted, movements_deleted, batches_deleted):
    parts = []

    if movements_deleted:
        label = "stock movement" if movements_deleted == 1 else "stock movements"
        parts.append(f"{movements_deleted} {label}")

    if batches_deleted:
        label = "batch record" if batches_deleted == 1 else "batch records"
        parts.append(f"{batches_deleted} {label}")

    if consumptions_deleted:
        label = "routing consumption record" if consumptions_deleted == 1 else "routing consumption records"
        parts.append(f"{consumptions_deleted} {label}")
        

    if parts:
        if len(parts) == 1:
            message = f"Deleted {parts[0]}."
        elif len(parts) == 2:
            message = f"Deleted {parts[0]} and {parts[1]}."
        else:
            message = "Deleted " + ", ".join(parts[:-1]) + f", and {parts[-1]}."
        flash(message, "success")
    else:
        flash(
            "There were no stock movement, batch, or routing consumption records to delete.",
            "info",
        )


@bp.route("/items/delete-all", methods=["POST"])
@require_roles("admin")
def delete_all_items():
    blocked_sources, dependent_item_ids = _gather_item_dependency_info()
    if blocked_sources:
        deletable_query = _deletable_items_query(dependent_item_ids)
        deletable_count = deletable_query.count()
        session["delete_all_prompt"] = {
            "blocked_sources": blocked_sources,
            "deletable_count": deletable_count,
        }

        joined = ", ".join(blocked_sources)
        flash(
            "Cannot delete all items because related records exist in the following "
            f"tables: {joined}. Remove those records first.",
            "danger",
        )
        return redirect(url_for("inventory.list_items"))

    deleted_count = Item.query.delete(synchronize_session=False)
    db.session.commit()

    if deleted_count:
        flash(f"All {deleted_count} items deleted successfully.", "success")
    else:
        flash("There were no items to delete.", "info")

    return redirect(url_for("inventory.list_items"))



@bp.route("/items/delete-available", methods=["POST"])
@require_roles("admin")
def delete_available_items():
    blocked_sources, dependent_item_ids = _gather_item_dependency_info()
    deletable_query = _deletable_items_query(dependent_item_ids)
    deletable_count = deletable_query.count()

    if deletable_count == 0:
        if blocked_sources:
            session["delete_all_prompt"] = {
                "blocked_sources": blocked_sources,
                "deletable_count": 0,
            }
        flash(
            "No items can be deleted while related records remain. Remove those "
            "records before trying again.",
            "info",
        )
        return redirect(url_for("inventory.list_items"))

    deleted = deletable_query.delete(synchronize_session=False)
    db.session.commit()
    flash(
        f"Deleted {deleted} item{'s' if deleted != 1 else ''} that had no related records.",
        "success",
    )

    remaining_sources, remaining_dependency_ids = _gather_item_dependency_info()
    remaining_deletable = _deletable_items_query(remaining_dependency_ids).count()
    if remaining_sources:
        session["delete_all_prompt"] = {
            "blocked_sources": remaining_sources,
            "deletable_count": remaining_deletable,
        }

    return redirect(url_for("inventory.list_items"))

@bp.route("/items/import", methods=["GET", "POST"])
def import_items():
    """
    Import items from CSV.
    - If sku exists → update the record.
    - If sku missing → auto-generate next sequential sku.
    - 'id' column (from export) is ignored if present.
    """
    if request.method == "POST":
        step = request.form.get("step")
        if step == "mapping":
            import_token = request.form.get("import_token", "")
            if not import_token:
                flash("No CSV data found. Please upload the file again.", "danger")
                return redirect(url_for("inventory.import_items"))


            csv_text = _load_import_csv("items", import_token)

            if csv_text is None:
                flash(
                    "Could not read the uploaded CSV data. Please upload the file again.",
                    "danger",
                )

                _remove_import_csv("items", import_token)

                return redirect(url_for("inventory.import_items"))

            selected_mappings = {}
            for field_cfg in ITEM_IMPORT_FIELDS:
                selected_header = request.form.get(f"mapping_{field_cfg['field']}", "")
                if selected_header:
                    selected_mappings[field_cfg["field"]] = selected_header

            missing_required = [
                field_cfg["label"]
                for field_cfg in ITEM_IMPORT_FIELDS
                if field_cfg["required"] and field_cfg["field"] not in selected_mappings
            ]
            if missing_required:
                flash(
                    "Please select a column for: " + ", ".join(missing_required) + ".",
                    "danger",
                )
                context = _prepare_item_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )

                context.update(
                    {
                        "submit_label": "Import Items",
                        "start_over_url": url_for("inventory.import_items"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)


            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded CSV does not contain a header row.", "danger")

                _remove_import_csv("items", import_token)

                return redirect(url_for("inventory.import_items"))

            invalid_columns = [
                header
                for header in selected_mappings.values()
                if header not in reader.fieldnames
            ]
            if invalid_columns:
                flash(
                    "Some selected columns could not be found in the file. Please upload the file again.",
                    "danger",
                )
                context = _prepare_item_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )

                context.update(
                    {
                        "submit_label": "Import Items",
                        "start_over_url": url_for("inventory.import_items"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)


            next_sku = _next_auto_sku_value()

            def extract(row, field):
                header = selected_mappings.get(field)
                if not header:
                    return ""
                value = row.get(header)
                return value if value is not None else ""

            count_new, count_updated = 0, 0
            for row in reader:
                sku = extract(row, "sku").strip()
                name = extract(row, "name").strip()
                unit = (
                    extract(row, "unit").strip()
                    if "unit" in selected_mappings
                    else "ea"
                )
                description = extract(row, "description").strip()
                min_stock_raw = (
                    extract(row, "min_stock") if "min_stock" in selected_mappings else 0
                )
                try:
                    min_stock = int(min_stock_raw or 0)
                except (TypeError, ValueError):
                    min_stock = 0

                has_type_column = "type" in selected_mappings
                item_type = (
                    extract(row, "type").strip() if has_type_column else None
                )

                has_notes_column = "notes" in selected_mappings
                if has_notes_column:
                    notes_raw = extract(row, "notes")
                    notes_clean = notes_raw.strip() if notes_raw is not None else ""
                    notes_value = notes_clean or None
                else:
                    notes_value = None

                has_list_price_column = "list_price" in selected_mappings
                has_last_unit_cost_column = "last_unit_cost" in selected_mappings
                has_item_class_column = "item_class" in selected_mappings

                list_price_value = (
                    _parse_decimal(extract(row, "list_price"))
                    if has_list_price_column
                    else None
                )
                last_unit_cost_value = (
                    _parse_decimal(extract(row, "last_unit_cost"))
                    if has_last_unit_cost_column
                    else None
                )
                item_class_value = (
                    (extract(row, "item_class") or "").strip()
                    if has_item_class_column
                    else None
                )

                existing = Item.query.filter_by(sku=sku).first() if sku else None
                if existing:
                    existing.name = name or existing.name
                    existing.unit = unit or existing.unit
                    existing.description = description or existing.description
                    existing.min_stock = min_stock or existing.min_stock
                    if has_type_column:
                        existing.type = item_type or None
                    if has_notes_column:
                        existing.notes = notes_value
                    if has_list_price_column:
                        existing.list_price = list_price_value
                    if has_last_unit_cost_column:
                        existing.last_unit_cost = last_unit_cost_value
                    if has_item_class_column:
                        existing.item_class = item_class_value or None
                    count_updated += 1
                else:
                    if not sku:
                        sku = str(next_sku)
                        next_sku += 1
                    item = Item(
                        sku=sku,
                        name=name,
                        type=(item_type or None) if has_type_column else None,
                        unit=unit,
                        description=description,
                        min_stock=min_stock,
                        notes=notes_value if has_notes_column else None,
                        list_price=list_price_value if has_list_price_column else None,
                        last_unit_cost=(
                            last_unit_cost_value if has_last_unit_cost_column else None
                        ),
                        item_class=(
                            (item_class_value or None) if has_item_class_column else None
                        ),
                    )
                    db.session.add(item)
                    count_new += 1

            db.session.commit()

            _remove_import_csv("items", import_token)

            flash(
                (
                    "Items imported: "
                    f"{count_new} new, {count_updated} updated "
                    "(extended fields processed)"
                ),
                "success",
            )
            return redirect(url_for("inventory.list_items"))

        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file uploaded", "danger")
            return redirect(request.url)

        try:
            csv_text = file.stream.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("CSV import files must be UTF-8 encoded.", "danger")
            return redirect(request.url)


        context = _prepare_item_import_mapping_context(csv_text)
        context.update(
            {
                "submit_label": "Import Items",
                "start_over_url": url_for("inventory.import_items"),
            }
        )
        import_token = context.get("import_token")
        if not import_token:
            flash(
                "Could not prepare the uploaded CSV. Please try again.",
                "danger",
            )
            return redirect(request.url)
        if not context["headers"]:
            _remove_import_csv("items", import_token)
            flash("Uploaded CSV does not contain a header row.", "danger")
            return redirect(request.url)

        return render_template("inventory/import_mapping.html", **context)


    return render_template("inventory/import_items.html")

@bp.route("/items/export")
def export_items():
    """
    Export items to CSV (without id).
    """
    items = Item.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "sku",
            "name",
            "type",
            "unit",
            "description",
            "min_stock",
            "notes",
            "list_price",
            "last_unit_cost",
            "item_class",
        ]
    )
    for i in items:
        writer.writerow(
            [
                i.sku,
                i.name,
                i.type or "",
                i.unit,
                i.description,
                i.min_stock,
                i.notes or "",
                _decimal_to_string(i.list_price),
                _decimal_to_string(i.last_unit_cost),
                i.item_class or "",
            ]
        )
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=items.csv"
    return response


############################
# LOCATION ROUTES
############################
@bp.route("/locations")
def list_locations():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    search = (request.args.get("search") or "").strip()
    like_pattern = f"%{search}%" if search else None

    locations_query = Location.query
    if like_pattern:
        matching_location_ids = (
            db.session.query(Movement.location_id)
            .join(Item, Item.id == Movement.item_id)
            .group_by(Movement.location_id, Movement.item_id)
            .having(func.sum(Movement.quantity) != 0)
            .filter(
                or_(
                    Item.sku.ilike(like_pattern),
                    Item.name.ilike(like_pattern),
                )
            )
        )

        locations_query = locations_query.filter(
            or_(
                Location.code.ilike(like_pattern),
                Location.description.ilike(like_pattern),
                Location.id.in_(matching_location_ids),
            )
        )

    pagination = locations_query.paginate(page=page, per_page=size, error_out=False)

    balances_query = (
        db.session.query(
            Movement.location_id,
            Movement.item_id,
            func.sum(Movement.quantity).label("on_hand"),
        )
        .join(Item, Item.id == Movement.item_id)
        .join(Location, Location.id == Movement.location_id)
        .group_by(Movement.location_id, Movement.item_id)
        .having(func.sum(Movement.quantity) != 0)
    )

    if like_pattern:
        balances_query = balances_query.filter(
            or_(
                Item.sku.ilike(like_pattern),
                Item.name.ilike(like_pattern),
                Location.code.ilike(like_pattern),
                Location.description.ilike(like_pattern),
            )
        )

    balances = balances_query.all()
    item_ids = {item_id for _, item_id, _ in balances}
    items = (
        {i.id: i for i in Item.query.filter(Item.id.in_(item_ids)).all()}
        if item_ids
        else {}
    )

    balances_by_location: dict[int, list[dict[str, Union[Item, int]]]] = defaultdict(list)
    for location_id, item_id, on_hand in balances:
        item = items.get(item_id)
        if not item:
            continue
        balances_by_location[location_id].append(
            {
                "item": item,
                "quantity": int(on_hand),
            }
        )

    for location_balances in balances_by_location.values():
        location_balances.sort(key=lambda entry: entry["item"].sku)

    return render_template(
        "inventory/list_locations.html",
        locations=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
        search=search,
        balances_by_location=balances_by_location,
    )


@bp.route("/locations/delete-all", methods=["POST"])
@require_roles("admin")
def delete_all_locations():
    has_movements = db.session.query(Movement.id).limit(1).first() is not None
    if has_movements:
        flash(
            "Cannot delete all locations because inventory movements reference them. "
            "Remove stock records before trying again.",
            "danger",
        )
        return redirect(url_for("inventory.list_locations"))

    deleted = Location.query.delete(synchronize_session=False)
    db.session.commit()

    if deleted:
        label = "location" if deleted == 1 else "locations"
        flash(f"Deleted {deleted} {label}.", "success")
    else:
        flash("There were no locations to delete.", "info")

    return redirect(url_for("inventory.list_locations"))


@bp.route("/location/add", methods=["GET", "POST"])
def add_location():
    if request.method == "POST":
        loc = Location(code=request.form["code"], description=request.form.get("description", ""))
        db.session.add(loc)
        db.session.commit()
        flash("Location added successfully", "success")
        return redirect(url_for("inventory.list_locations"))
    return render_template("inventory/add_location.html")


@bp.route("/location/<int:location_id>/edit", methods=["GET", "POST"])
@require_roles("admin")
def edit_location(location_id):
    location = Location.query.get_or_404(location_id)

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        description = (request.form.get("description") or "").strip()
        errors = []

        if not code:
            errors.append("Location code is required.")
        elif code != location.code:
            existing = Location.query.filter_by(code=code).first()
            if existing:
                errors.append("Another location with that code already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "inventory/edit_location.html",
                location=location,
                form_code=code,
                form_description=description,
            )

        location.code = code
        location.description = description
        db.session.commit()
        flash(f"Location {location.code} updated successfully.", "success")
        return redirect(url_for("inventory.list_locations"))

    return render_template(
        "inventory/edit_location.html",
        location=location,
        form_code=location.code,
        form_description=location.description or "",
    )


@bp.route("/location/<int:location_id>/print-label", methods=["POST"])
@require_roles("admin")
def print_location_label(location_id: int):
    location = Location.query.get_or_404(location_id)

    from invapp.printing.labels import build_location_label_context
    from invapp.printing.zebra import print_label_for_process

    context = build_location_label_context(location)
    if print_label_for_process("LocationLabel", context):
        flash(f"Label queued for location {location.code}.", "success")
    else:
        flash("Failed to print location label.", "warning")

    return redirect(url_for("inventory.edit_location", location_id=location.id))


@bp.route("/location/<int:location_id>/delete", methods=["POST"])
@require_roles("admin")
def delete_location(location_id):
    location = Location.query.get_or_404(location_id)
    has_movements = Movement.query.filter_by(location_id=location.id).first() is not None

    if has_movements:
        flash(
            "Cannot delete location because inventory movements reference it.",
            "danger",
        )
        return redirect(url_for("inventory.edit_location", location_id=location.id))

    code = location.code
    db.session.delete(location)
    db.session.commit()
    flash(f"Location {code} deleted successfully.", "success")
    return redirect(url_for("inventory.list_locations"))


@bp.route("/locations/import", methods=["GET", "POST"])
def import_locations():
    """
    Import locations from CSV.
    - If code exists → update description.
    - 'id' column is ignored.
    """
    if request.method == "POST":
        step = request.form.get("step")
        if step == "mapping":
            import_token = request.form.get("import_token", "")
            if not import_token:
                flash("No CSV data found. Please upload the file again.", "danger")
                return redirect(url_for("inventory.import_locations"))

            csv_text = _load_import_csv("locations", import_token)
            if csv_text is None:
                flash(
                    "Could not read the uploaded CSV data. Please upload the file again.",
                    "danger",
                )
                _remove_import_csv("locations", import_token)
                return redirect(url_for("inventory.import_locations"))

            selected_mappings = {}
            for field_cfg in LOCATION_IMPORT_FIELDS:
                selected_header = request.form.get(f"mapping_{field_cfg['field']}", "")
                if selected_header:
                    selected_mappings[field_cfg["field"]] = selected_header

            missing_required = [
                field_cfg["label"]
                for field_cfg in LOCATION_IMPORT_FIELDS
                if field_cfg["required"] and field_cfg["field"] not in selected_mappings
            ]
            if missing_required:
                flash(
                    "Please select a column for: " + ", ".join(missing_required) + ".",
                    "danger",
                )
                context = _prepare_location_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )
                context.update(
                    {
                        "mapping_title": "Map Location Columns",
                        "submit_label": "Import Locations",
                        "start_over_url": url_for("inventory.import_locations"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)

            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded CSV does not contain a header row.", "danger")
                _remove_import_csv("locations", import_token)
                return redirect(url_for("inventory.import_locations"))

            invalid_columns = [
                header
                for header in selected_mappings.values()
                if header not in reader.fieldnames
            ]
            if invalid_columns:
                flash(
                    "Some selected columns could not be found in the file. Please upload the file again.",
                    "danger",
                )
                context = _prepare_location_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )
                context.update(
                    {
                        "mapping_title": "Map Location Columns",
                        "submit_label": "Import Locations",
                        "start_over_url": url_for("inventory.import_locations"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)

            def extract(row, field):
                header = selected_mappings.get(field)
                if not header:
                    return ""
                value = row.get(header)
                return value if value is not None else ""

            count_new, count_updated = 0, 0
            for row in reader:
                code = extract(row, "code").strip()
                if not code:
                    continue
                description = extract(row, "description").strip()

                existing = Location.query.filter_by(code=code).first()
                if existing:
                    if description:
                        existing.description = description
                    count_updated += 1
                else:
                    loc = Location(code=code, description=description)
                    db.session.add(loc)
                    count_new += 1

            db.session.commit()
            _remove_import_csv("locations", import_token)
            flash(
                f"Locations imported: {count_new} new, {count_updated} updated",
                "success",
            )
            return redirect(url_for("inventory.list_locations"))

        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file uploaded", "danger")
            return redirect(request.url)

        try:
            csv_text = file.stream.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("CSV import files must be UTF-8 encoded.", "danger")
            return redirect(request.url)

        context = _prepare_location_import_mapping_context(csv_text)
        context.update(
            {
                "mapping_title": "Map Location Columns",
                "submit_label": "Import Locations",
                "start_over_url": url_for("inventory.import_locations"),
            }
        )
        import_token = context.get("import_token")
        if not import_token:
            flash(
                "Could not prepare the uploaded CSV. Please try again.",
                "danger",
            )
            return redirect(request.url)
        if not context["headers"]:
            _remove_import_csv("locations", import_token)
            flash("Uploaded CSV does not contain a header row.", "danger")
            return redirect(request.url)

        return render_template("inventory/import_mapping.html", **context)

    return render_template("inventory/import_locations.html")


@bp.route("/locations/export")
def export_locations():
    """
    Export locations to CSV (without id).
    """
    locations = Location.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["code", "description"])
    for l in locations:
        writer.writerow([l.code, l.description])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=locations.csv"
    return response


############################
# STOCK ROUTES
############################
@bp.route("/stock")
def list_stock():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    status = request.args.get("status", "all")
    search = request.args.get("search", "")
    like_pattern = f"%{search}%" if search else None
    if status not in {"all", "low", "near"}:
        status = "all"
    rows_query = (
        db.session.query(
            Movement.item_id,
            Movement.batch_id,
            Movement.location_id,
            func.sum(Movement.quantity).label("on_hand")
        )
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(Batch, Batch.id == Movement.batch_id)
        .outerjoin(Location, Location.id == Movement.location_id)
        .group_by(Movement.item_id, Movement.batch_id, Movement.location_id)
        .having(func.sum(Movement.quantity) != 0)
    )
    if like_pattern:
        rows_query = rows_query.filter(
            or_(
                Item.sku.ilike(like_pattern),
                Item.name.ilike(like_pattern),
                Batch.lot_number.ilike(like_pattern),
                Location.code.ilike(like_pattern),
            )
        )
    pagination = rows_query.paginate(page=page, per_page=size, error_out=False)
    rows = pagination.items

    totals_query = (
        db.session.query(
            Movement.item_id,
            func.sum(Movement.quantity).label("total_on_hand")
        )
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(Batch, Batch.id == Movement.batch_id)
        .outerjoin(Location, Location.id == Movement.location_id)
    )
    if like_pattern:
        totals_query = totals_query.filter(
            or_(
                Item.sku.ilike(like_pattern),
                Item.name.ilike(like_pattern),
                Batch.lot_number.ilike(like_pattern),
                Location.code.ilike(like_pattern),
            )
        )
    totals_query = (
        totals_query
        .group_by(Movement.item_id)
        .all()
    )
    totals_map = {item_id: int(total or 0) for item_id, total in totals_query}

    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    def matches_status(item_obj, total_qty):
        if status == "all":
            return True
        if not item_obj:
            return False
        min_stock = item_obj.min_stock or 0
        multiplier = 1.05 if status == "low" else 1.25
        return total_qty < (min_stock * multiplier)

    totals = {
        item_id: total
        for item_id, total in totals_map.items()
        if matches_status(items.get(item_id), total)
    }

    balances = []
    for item_id, batch_id, location_id, on_hand in rows:
        item = items.get(item_id)
        total_on_hand = totals_map.get(item_id, 0)
        if not matches_status(item, total_on_hand):
            continue
        balances.append({
            "item": item,
            "batch": batches.get(batch_id) if batch_id else None,
            "location": locations.get(location_id),
            "on_hand": int(on_hand),
            "total_on_hand": total_on_hand,
        })

    return render_template(
        "inventory/list_stock.html",
        balances=balances,
        totals=totals,
        items=items,
        status=status,
        page=page,
        size=size,
        pages=pagination.pages,
        search=search,
    )


@bp.route("/stock/delete-all", methods=["POST"])
@require_roles("admin")
def delete_all_stock():
    consumptions_deleted, movements_deleted, batches_deleted = _delete_stock_records()
    _flash_stock_deletion_summary(consumptions_deleted, movements_deleted, batches_deleted)

    return redirect(url_for("inventory.list_stock"))


@bp.route("/stock/adjust", methods=["GET", "POST"])
def adjust_stock():
    items = Item.query.all()
    locations = Location.query.all()

    if request.method == "POST":
        sku = request.form["sku"].strip()
        qty = int(request.form["quantity"])
        location_id = int(request.form["location_id"])
        person = request.form.get("person", "").strip() or None
        reference = request.form.get("reference", "").strip() or "Manual Adjustment"

        item = Item.query.filter_by(sku=sku).first()
        if not item:
            flash(f"Item with SKU {sku} not found.", "danger")
            return redirect(url_for("inventory.adjust_stock"))

        batch_id = None
        if lot_number:
            batch = Batch.query.filter_by(item_id=item.id, lot_number=lot_number).first()
            if not batch:
                batch = Batch(item_id=item.id, lot_number=lot_number, quantity=0)
                db.session.add(batch)
                db.session.flush()
            batch_id = batch.id
            batch.quantity = (batch.quantity or 0) + qty

        mv = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=location_id,
            quantity=qty,
            movement_type="ADJUST",
            person=person,
            reference=reference
        )
        db.session.add(mv)
        db.session.commit()

        flash(f"Adjustment saved for SKU {sku}", "success")
        return redirect(url_for("inventory.list_stock"))

    return render_template("inventory/adjust_stock.html", items=items, locations=locations)


@bp.route("/stock/import", methods=["GET", "POST"])
def import_stock():
    """
    Bulk import stock adjustments from CSV.
    Expected CSV columns: sku, location_code, quantity, lot_number (optional), person (optional), reference (optional)
    """
    if request.method == "POST":
        step = request.form.get("step")
        if step == "mapping":
            import_token = request.form.get("import_token", "")
            if not import_token:
                flash("No CSV data found. Please upload the file again.", "danger")
                return redirect(url_for("inventory.import_stock"))

            csv_text = _load_import_csv("stock", import_token)
            if csv_text is None:
                flash(
                    "Could not read the uploaded CSV data. Please upload the file again.",
                    "danger",
                )
                _remove_import_csv("stock", import_token)
                return redirect(url_for("inventory.import_stock"))

            selected_mappings = {}
            for field_cfg in STOCK_IMPORT_FIELDS:
                selected_header = request.form.get(f"mapping_{field_cfg['field']}", "")
                if selected_header:
                    selected_mappings[field_cfg["field"]] = selected_header

            missing_required = [
                field_cfg["label"]
                for field_cfg in STOCK_IMPORT_FIELDS
                if field_cfg["required"] and field_cfg["field"] not in selected_mappings
            ]
            if missing_required:
                flash(
                    "Please select a column for: " + ", ".join(missing_required) + ".",
                    "danger",
                )
                context = _prepare_stock_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )
                context.update(
                    {
                        "mapping_title": "Map Stock Adjustment Columns",
                        "submit_label": "Import Stock Adjustments",
                        "start_over_url": url_for("inventory.import_stock"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)

            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded CSV does not contain a header row.", "danger")
                _remove_import_csv("stock", import_token)
                return redirect(url_for("inventory.import_stock"))

            invalid_columns = [
                header
                for header in selected_mappings.values()
                if header not in reader.fieldnames
            ]
            if invalid_columns:
                flash(
                    "Some selected columns could not be found in the file. Please upload the file again.",
                    "danger",
                )
                context = _prepare_stock_import_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )
                context.update(
                    {
                        "mapping_title": "Map Stock Adjustment Columns",
                        "submit_label": "Import Stock Adjustments",
                        "start_over_url": url_for("inventory.import_stock"),
                    }
                )
                return render_template("inventory/import_mapping.html", **context)

            item_map = {i.sku: i for i in Item.query.all()}
            loc_map = {l.code: l for l in Location.query.all()}

            placeholder_location = _ensure_placeholder_location(loc_map)

            def extract(row, field):
                header = selected_mappings.get(field)
                if not header:
                    return ""
                value = row.get(header)
                return value if value is not None else ""

            count_new, count_updated = 0, 0
            for row in reader:
                sku = extract(row, "sku").strip()
                if not sku:
                    continue

                quantity_raw = extract(row, "quantity").strip()
                try:
                    qty = int(quantity_raw)
                except (TypeError, ValueError):
                    continue

                loc_code = extract(row, "location_code").strip()
                lot_number = extract(row, "lot_number").strip() or None
                person = extract(row, "person").strip() or None
                reference = extract(row, "reference").strip() or "Bulk Adjust"

                item = item_map.get(sku)
                if not item:
                    continue

                location = loc_map.get(loc_code) if loc_code else None
                if not location:
                    location = placeholder_location

                batch = None
                if lot_number:
                    batch = Batch.query.filter_by(
                        item_id=item.id, lot_number=lot_number
                    ).first()
                    if not batch:
                        batch = Batch(item_id=item.id, lot_number=lot_number, quantity=0)
                        db.session.add(batch)
                        db.session.flush()
                        count_new += 1
                    else:
                        count_updated += 1
                    batch.quantity = (batch.quantity or 0) + qty

                mv = Movement(
                    item_id=item.id,
                    batch_id=batch.id if batch else None,
                    location_id=location.id,
                    quantity=qty,
                    movement_type="ADJUST",
                    person=person,
                    reference=reference,
                )
                db.session.add(mv)

            db.session.commit()
            _remove_import_csv("stock", import_token)
            flash(
                f"Stock adjustments processed: {count_new} new batches, {count_updated} updated batches",
                "success",
            )
            return redirect(url_for("inventory.list_stock"))

        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file uploaded", "danger")
            return redirect(request.url)

        try:
            csv_text = file.stream.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("CSV import files must be UTF-8 encoded.", "danger")
            return redirect(request.url)

        context = _prepare_stock_import_mapping_context(csv_text)
        context.update(
            {
                "mapping_title": "Map Stock Adjustment Columns",
                "submit_label": "Import Stock Adjustments",
                "start_over_url": url_for("inventory.import_stock"),
            }
        )
        import_token = context.get("import_token")
        if not import_token:
            flash(
                "Could not prepare the uploaded CSV. Please try again.",
                "danger",
            )
            return redirect(request.url)
        if not context["headers"]:
            _remove_import_csv("stock", import_token)
            flash("Uploaded CSV does not contain a header row.", "danger")
            return redirect(request.url)

        return render_template("inventory/import_mapping.html", **context)

    return render_template("inventory/import_stock.html")


@bp.route("/stock/export")
def export_stock():
    """
    Export current stock balances to CSV (without id).
    """
    rows = (
        db.session.query(
            Movement.item_id,
            Movement.batch_id,
            Movement.location_id,
            func.sum(Movement.quantity).label("on_hand")
        )
        .group_by(Movement.item_id, Movement.batch_id, Movement.location_id)
        .having(func.sum(Movement.quantity) != 0)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["sku", "item_name", "location", "lot_number", "on_hand"])

    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    for item_id, batch_id, location_id, on_hand in rows:
        sku = items[item_id].sku if item_id in items else "UNKNOWN"
        name = items[item_id].name if item_id in items else "UNKNOWN"
        loc = locations[location_id].code if location_id in locations else "UNKNOWN"
        lot = batches[batch_id].lot_number if batch_id and batch_id in batches else "-"
        writer.writerow([sku, name, loc, lot, on_hand])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=stock.csv"
    return response



############################
# RECEIVING ROUTES
############################
@bp.route("/receiving", methods=["GET", "POST"])
def receiving():
    locations = Location.query.all()
    form_defaults = {
        "sku": request.args.get("sku", "").strip(),
        "qty": request.args.get("qty", "").strip(),
        "person": request.args.get("person", "").strip(),
        "po_number": request.args.get("po_number", "").strip(),
        "location_id": request.args.get("location_id", "").strip(),
    }

    default_location = None
    item_details = None
    if form_defaults["sku"]:
        item_for_defaults = Item.query.filter_by(sku=form_defaults["sku"]).first()
        default_location = getattr(item_for_defaults, "default_location", None)
        if item_for_defaults:
            item_details = {
                "name": item_for_defaults.name or "",
                "description": item_for_defaults.description or "",
            }
        if not form_defaults["location_id"] and default_location:
            form_defaults["location_id"] = str(default_location.id)

    if request.method == "POST":
        sku = request.form["sku"].strip()
        defer_qty = (
            current_user.is_authenticated
            and current_user.has_role("admin")
            and request.form.get("defer_qty") == "1"
        )
        qty_raw = request.form.get("qty", "").strip()
        person = request.form["person"].strip()
        po_number = request.form.get("po_number", "").strip() or None
        location_id = int(request.form["location_id"])

        if defer_qty:
            qty = 0
        else:
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                flash("Quantity is required unless deferred for an admin.", "danger")
                return redirect(url_for("inventory.receiving"))

        item = Item.query.filter_by(sku=sku).first()
        if not item:
            flash(f"Item with SKU {sku} not found.", "danger")
            return redirect(url_for("inventory.receiving"))

        # ?? Auto-generate lot number: SKU-YYMMDD-##
        today_str = datetime.now().strftime("%y%m%d")
        base_lot = f"{item.sku}-{today_str}"

        existing_lots = Batch.query.filter(
            Batch.item_id == item.id,
            Batch.lot_number.like(f"{base_lot}-%")
        ).count()

        seq_num = existing_lots + 1
        lot_number = f"{base_lot}-{seq_num:02d}"

        # Create or update batch
        batch = Batch(item_id=item.id, lot_number=lot_number, quantity=0)
        db.session.add(batch)
        db.session.flush()
        batch_id = batch.id
        batch.quantity = (batch.quantity or 0) + qty
        if po_number:
            batch.purchase_order = po_number

        # Record movement
        reference = "PO Receipt" if po_number else "Receipt"
        if defer_qty:
            reference += " (quantity pending)"

        mv = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=location_id,
            quantity=qty,
            movement_type="RECEIPT",
            person=person,
            po_number=po_number,
            reference=reference
        )
        db.session.add(mv)
        db.session.commit()

        if defer_qty:
            flash(f"Receiving recorded! Lot: {lot_number}", "success")
            flash(
                "Receiving recorded without a quantity. Update the batch once the count is known.",
                "info",
            )
            return redirect(url_for("inventory.receiving"))

        try:
            location = Location.query.get(location_id)
            if not _print_batch_receipt_label(
                batch,
                item,
                qty,
                location,
                po_number,
            ):
                flash("Failed to print receiving label.", "warning")
        except Exception:
            flash("Failed to print receiving label.", "warning")

        flash(f"Receiving recorded! Lot: {lot_number}", "success")
        return redirect(url_for("inventory.receiving"))

    # Display recent receipts
    records = (
        Movement.query.options(
            joinedload(Movement.item).load_only(Item.sku, Item.name),
            joinedload(Movement.location).load_only(Location.code),
            joinedload(Movement.batch).load_only(Batch.lot_number),
        )
        .filter_by(movement_type="RECEIPT")
        .order_by(Movement.date.desc())
        .limit(50)
        .all()
    )

    can_defer_without_qty = current_user.is_authenticated and current_user.has_role("admin")

    return render_template(
        "inventory/receiving.html",
        records=records,
        locations=locations,
        form_defaults=form_defaults,
        default_location=default_location,
        item_details=item_details,
        can_defer_without_qty=can_defer_without_qty,
    )


@bp.post("/receiving/<int:receipt_id>/reprint")
def reprint_receiving_label(receipt_id: int):
    """Reprint a previously generated receiving label."""
    rec = (
        Movement.query.options(
            joinedload(Movement.item).load_only(Item.sku, Item.name, Item.unit),
            joinedload(Movement.location).load_only(Location.code),
            joinedload(Movement.batch),
        )
        .filter_by(id=receipt_id, movement_type="RECEIPT")
        .first_or_404()
    )

    item = rec.item
    qty = rec.quantity
    batch = rec.batch
    location = rec.location

    try:
        if not _print_batch_receipt_label(
            batch,
            item,
            qty,
            location,
            rec.po_number,
        ):
            flash("Failed to print receiving label.", "warning")
        else:
            flash("Label reprinted.", "success")
    except Exception:
        flash("Failed to print receiving label.", "warning")

    return redirect(url_for("inventory.receiving"))


def _print_batch_receipt_label(
    batch: Union[Batch, Mapping[str, object], None],
    item: Item,
    qty: int,
    location: Optional[Location],
    po_number: Optional[str],
) -> bool:
    """Render and queue the Batch Label for a receiving transaction.

    Both the receiving workflow and the reprint action should emit the
    "BatchCreated" process label so operators receive the same output each time.
    This helper normalises the context that is passed into the label renderer so
    that lot, quantity, and location details are always available even when the
    movement record is missing a fully-populated ``Batch`` relationship.
    """

    from invapp.printing.labels import build_batch_label_context
    from invapp.printing.zebra import print_label_for_process

    lot_number = (
        getattr(batch, "lot_number", None)
        if batch is not None
        else None
    ) or getattr(item, "sku", "")

    batch_source: Union[Batch, Mapping[str, object]]
    if batch is None:
        batch_source = {
            "lot_number": lot_number,
            "quantity": qty,
        }
    else:
        batch_source = batch

    context = build_batch_label_context(
        batch_source,
        item=item,
        quantity=qty,
        location=location,
        po_number=po_number,
    )

    return print_label_for_process("BatchCreated", context)

############################
# MOVE / TRANSFER ROUTES
############################
@bp.route("/move", methods=["GET", "POST"])
def move_home():
    items = Item.query.all()
    locations = Location.query.all()
    batches = Batch.query.all()
    prefill_sku = request.values.get("sku", "").strip()

    if request.method == "POST":
        sku = request.form["sku"].strip()
        batch_id = int(request.form["batch_id"])
        from_loc_id = int(request.form["from_location_id"])
        to_loc_id = int(request.form["to_location_id"])
        qty = int(request.form["qty"])
        person = request.form["person"].strip()
        reference = request.form.get("reference", "Stock Transfer")

        item = Item.query.filter_by(sku=sku).first()
        batch = Batch.query.get(batch_id)
        if not item or not batch:
            flash("Invalid SKU or Batch selected.", "danger")
            return redirect(url_for("inventory.move_home"))

        # Ensure valid stock in from location
        from_balance = (
            db.session.query(func.sum(Movement.quantity))
            .filter_by(item_id=item.id, batch_id=batch_id, location_id=from_loc_id)
            .scalar()
        ) or 0

        if qty > from_balance:
            flash("Not enough stock in the selected batch/location.", "danger")
            return redirect(url_for("inventory.move_home"))

        # Create MOVE_OUT (negative) and MOVE_IN (positive)
        mv_out = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=from_loc_id,
            quantity=-qty,
            movement_type="MOVE_OUT",
            person=person,
            reference=reference,
        )
        mv_in = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=to_loc_id,
            quantity=qty,
            movement_type="MOVE_IN",
            person=person,
            reference=reference,
        )
        db.session.add(mv_out)
        db.session.add(mv_in)
        db.session.commit()

        flash(f"Moved {qty} of {sku} (Lot {batch.lot_number}) to new location.", "success")
        return redirect(url_for("inventory.move_home"))

    # Recent moves
    records = (
        Movement.query
        .filter(Movement.movement_type.in_(["MOVE_OUT", "MOVE_IN"]))
        .order_by(Movement.date.desc())
        .limit(50)
        .all()
    )
    items_map = {i.id: i for i in Item.query.all()}
    locations_map = {l.id: l for l in Location.query.all()}
    batches_map = {b.id: b for b in Batch.query.all()}

    lookup_template = url_for("inventory.lookup_item_api", sku="__SKU__")

    return render_template(
        "inventory/move.html",
        items=items,
        locations=locations,
        batches=batches,
        records=records,
        items_map=items_map,
        locations_map=locations_map,
        batches_map=batches_map,
        prefill_sku=prefill_sku,
        lookup_template=lookup_template,
    )


############################
# ISSUE / MOVE / COUNT / HISTORY
############################

@bp.route("/history")
def history_home():
    records = (
        Movement.query
        .order_by(Movement.date.desc())
        .limit(200)
        .all()
    )
    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    return render_template(
        "inventory/history.html",
        records=records,
        items=items,
        locations=locations,
        batches=batches
    )


@bp.route("/history/delete-all", methods=["POST"])
@require_roles("admin")
def delete_all_history():
    consumptions_deleted, movements_deleted, batches_deleted = _delete_stock_records()
    _flash_stock_deletion_summary(consumptions_deleted, movements_deleted, batches_deleted)

    return redirect(url_for("inventory.history_home"))


@bp.route("/history/export")
def export_history():
    """
    Export all transactions (Movement table) to CSV.
    """
    query = (
        db.session.query(
            Movement.date,
            Item.sku,
            Item.name,
            Movement.movement_type,
            Movement.quantity,
            Location.code,
            Batch.lot_number,
            Movement.person,
            Movement.reference,
            Movement.po_number,
        )
        .join(Item, Movement.item_id == Item.id)
        .join(Location, Movement.location_id == Location.id)
        .outerjoin(Batch, Movement.batch_id == Batch.id)
        .order_by(Movement.date.desc())
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date", "sku", "item_name", "movement_type", "quantity",
        "location", "lot_number", "person", "reference", "po_number"
    ])

    for (
        date,
        sku,
        item_name,
        movement_type,
        quantity,
        location_code,
        lot_number,
        person,
        reference,
        po_number,
    ) in query:
        writer.writerow([
            date.strftime("%Y-%m-%d %H:%M"),
            sku,
            item_name,
            movement_type,
            quantity,
            location_code,
            lot_number or "-",
            person or "-",
            reference or "-",
            po_number or "-",
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=transaction_history.csv"
    return response
