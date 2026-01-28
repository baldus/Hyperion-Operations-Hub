import csv
import io
import json
import math
import os
import re
import secrets
import tempfile
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
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
from sqlalchemy import asc, case, desc, func, inspect, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased, joinedload, lazyload, load_only
from sqlalchemy.orm.exc import DetachedInstanceError

from invapp.auth import blueprint_page_guard
from invapp.login import current_user, login_required
from invapp.permissions import resolve_edit_roles
from invapp.security import require_admin_or_superuser, require_any_role, require_roles
from invapp.superuser import is_superuser, superuser_required
from invapp.models import (
    AdminAuditLog,
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
    PhysicalInventorySnapshot,
    PhysicalInventorySnapshotLine,
    PurchaseRequest,
    Reservation,
    RoutingStepConsumption,
    User,
    db,
)
from invapp.services.physical_inventory import (
    NormalizationOptions,
    aggregate_matched_rows,
    build_missing_item_candidates,
    get_item_field_samples,
    get_item_text_fields,
    match_upload_rows,
    normalize_match_value,
)
from invapp.services.stock_transfer import (
    MoveLineRequest,
    PENDING_RECEIPT_MARKER,
    get_location_inventory_lines,
    move_inventory_lines,
    pending_receipt_case,
)
from invapp.services.item_locations import apply_smart_item_locations
from invapp.utils.csv_export import export_rows_to_csv
from invapp.utils.csv_schema import (
    ITEMS_CSV_COLUMNS,
    ITEMS_HEADER_ALIASES,
    STOCK_CSV_COLUMNS,
    STOCK_HEADER_ALIASES,
    expected_headers,
    resolve_import_mappings,
)
from invapp.utils.tabular_import import TabularImportError, parse_tabular_upload, preview_csv_text
from invapp.utils.location_parser import parse_location_code
from invapp.utils.physical_inventory import get_location_aisle
from werkzeug.utils import secure_filename

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

bp.before_request(blueprint_page_guard("inventory"))


UNASSIGNED_LOCATION_CODE = "UNASSIGNED"
PLACEHOLDER_CREATION_MAX_RETRIES = 5
PLACEHOLDER_CREATION_INITIAL_BACKOFF = 0.05
LOCATION_SEARCH_LIMIT = 25


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


def _format_location_label(location: Location) -> str:
    description = (location.description or "").strip()
    if description:
        return f"{location.code} — {description}"
    return location.code


def _resolve_location_from_form(value: str | None) -> Location | None:
    if not value:
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    try:
        location_id = int(raw_value)
    except (TypeError, ValueError):
        location_id = None

    if location_id is not None:
        location = db.session.get(Location, location_id)
        if location:
            return location

    return Location.query.filter(func.lower(Location.code) == raw_value.lower()).first()


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
    {"field": "id", "label": "item_id", "required": False},
    {"field": "sku", "label": "sku", "required": False},
    {"field": "name", "label": "name", "required": True},
    {"field": "type", "label": "type", "required": False},
    {"field": "unit", "label": "unit", "required": False},
    {"field": "description", "label": "description", "required": False},
    {"field": "min_stock", "label": "min_stock", "required": False},
    {"field": "notes", "label": "notes", "required": False},
    {"field": "list_price", "label": "list_price", "required": False},
    {"field": "last_unit_cost", "label": "last_unit_cost", "required": False},
    {"field": "item_class", "label": "item_class", "required": False},
    {"field": "default_location_id", "label": "default_location_id", "required": False},
    {
        "field": "default_location_code",
        "label": "default_location_code",
        "required": False,
    },
    {
        "field": "secondary_location_id",
        "label": "secondary_location_id",
        "required": False,
    },
    {
        "field": "secondary_location_code",
        "label": "secondary_location_code",
        "required": False,
    },
    {
        "field": "point_of_use_location_id",
        "label": "point_of_use_location_id",
        "required": False,
    },
    {
        "field": "point_of_use_location_code",
        "label": "point_of_use_location_code",
        "required": False,
    },
]


LOCATION_IMPORT_FIELDS = [
    {"field": "code", "label": "Location Code", "required": True},
    {"field": "description", "label": "Description", "required": False},
]

STOCK_IMPORT_FIELDS = [
    {"field": "item_id", "label": "item_id", "required": False},
    {"field": "sku", "label": "sku", "required": False},
    {"field": "name", "label": "name", "required": False},
    {"field": "location_id", "label": "location_id", "required": False},
    {"field": "location_code", "label": "location_code", "required": False},
    {"field": "batch_id", "label": "batch_id", "required": False},
    {"field": "lot_number", "label": "lot_number", "required": False},
    {"field": "quantity", "label": "quantity", "required": True},
    {"field": "person", "label": "person", "required": False},
    {"field": "reference", "label": "reference", "required": False},
    {"field": "received_date", "label": "received_date", "required": False},
    {"field": "expiration_date", "label": "expiration_date", "required": False},
    {"field": "supplier_name", "label": "supplier_name", "required": False},
    {"field": "supplier_code", "label": "supplier_code", "required": False},
    {"field": "purchase_order", "label": "purchase_order", "required": False},
    {"field": "notes", "label": "notes", "required": False},
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


def _parse_int(value: str) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_location_id(value: str | None) -> Optional[int]:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_item_location_duplicates(
    primary_location_id: Optional[int],
    secondary_location_id: Optional[int],
    point_of_use_location_id: Optional[int],
) -> list[str]:
    errors = []
    if (
        primary_location_id
        and secondary_location_id
        and primary_location_id == secondary_location_id
    ):
        errors.append("Primary and secondary locations must be different.")
    if (
        primary_location_id
        and point_of_use_location_id
        and primary_location_id == point_of_use_location_id
    ):
        errors.append("Primary and point-of-use locations must be different.")
    if (
        secondary_location_id
        and point_of_use_location_id
        and secondary_location_id == point_of_use_location_id
    ):
        errors.append("Secondary and point-of-use locations must be different.")
    return errors


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _log_unmapped_headers(namespace: str, headers: list[str], mapped: dict[str, str]):
    unmapped = sorted({header for header in headers if header not in mapped.values()})
    if unmapped:
        current_app.logger.warning(
            "CSV import for %s ignored unmapped columns: %s",
            namespace,
            ", ".join(unmapped),
        )

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


PHYSICAL_INVENTORY_HEADER_ALIASES = {
    "item_name": ["item name", "name", "item", "product", "item_name"],
    "description": ["description", "item description", "item_desc"],
    "quantity": ["quantity", "qty", "on_hand", "onhand", "total"],
}

PHYSICAL_INVENTORY_DUPLICATE_STRATEGIES = {
    "sum": "Sum duplicate quantities",
    "keep_first": "Keep the first quantity",
    "keep_last": "Keep the last quantity",
}


def _auto_map_physical_inventory_headers(headers: list[str]) -> dict[str, str]:
    normalized_headers = {header.strip().lower(): header for header in headers}
    selections: dict[str, str] = {}
    for key, aliases in PHYSICAL_INVENTORY_HEADER_ALIASES.items():
        for alias in aliases:
            header = normalized_headers.get(alias.lower())
            if header:
                selections[key] = header
                break
    return selections


def _prepare_physical_inventory_mapping_context(
    csv_text: str,
    selected_mappings: dict[str, str] | None = None,
    token: str | None = None,
):
    headers, sample_rows = preview_csv_text(csv_text)
    import_token = _store_import_csv("physical_inventory", csv_text, token=token)
    return {
        "headers": headers,
        "sample_rows": sample_rows,
        "import_token": import_token,
        "selected_mappings": selected_mappings or {},
        "item_fields": get_item_text_fields(),
        "duplicate_strategies": PHYSICAL_INVENTORY_DUPLICATE_STRATEGIES,
    }


def _aisle_sort_key(value: str | None) -> tuple:
    if value is None:
        return (1, "", [])
    text = str(value).strip()
    if not text:
        return (1, "", [])
    parts = re.split(r"(\d+)", text)
    normalized_parts = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            normalized_parts.append((0, int(part)))
        else:
            normalized_parts.append((1, part.lower()))
    return (0, text.lower(), normalized_parts)


def _normalize_aisle_label(value: str | None) -> str:
    text = str(value).strip() if value is not None else ""
    return text or "UNASSIGNED"


def _build_physical_inventory_count_rows(
    lines: list[PhysicalInventorySnapshotLine],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in lines:
        item = line.item
        location = item.default_location if item else None
        aisle = _normalize_aisle_label(get_location_aisle(location))
        rows.append(
            {
                "aisle": aisle,
                "location_code": location.code if location else "UNLOCATED",
                "location_description": location.description if location else "",
                "item_name": item.name if item else "Unknown Item",
                "item_description": item.description if item else "",
                "sku": item.sku if item else "",
                "erp_quantity": line.erp_quantity,
                "counted_quantity": "",
                "notes": "",
            }
        )
    return rows


def _create_missing_inventory_items(
    candidates: list[dict[str, object]],
    *,
    primary_item_field: str,
    options: NormalizationOptions,
    request_ip: str | None,
) -> int:
    if not candidates:
        return 0

    normalized_existing = {
        normalize_match_value(item.name, options)
        for item in Item.query.with_entities(Item.name).all()
        if item.name
    }

    next_sku_value = _next_auto_sku_value()
    created_items: list[Item] = []
    for candidate in candidates:
        name_value = candidate.get("name", "")
        normalized_name = normalize_match_value(name_value, options)
        if not normalized_name or normalized_name in normalized_existing:
            continue

        item = Item(sku=str(next_sku_value), name=name_value)
        next_sku_value += 1
        normalized_existing.add(normalized_name)

        if primary_item_field and primary_item_field != "name":
            if hasattr(Item, primary_item_field):
                setattr(item, primary_item_field, name_value)

        fields = candidate.get("fields") or {}
        for field, value in fields.items():
            if field in {"id", "sku", "name"}:
                continue
            if hasattr(Item, field) and value:
                setattr(item, field, value)

        created_items.append(item)
        db.session.add(item)

    if created_items:
        audit_log = AdminAuditLog(
            user_id=current_user.id if current_user else None,
            action="physical_inventory_create_items",
            note=f"Created {len(created_items)} items from physical inventory import.",
            request_ip=request_ip,
        )
        db.session.add(audit_log)

    return len(created_items)


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


############################
# PHYSICAL INVENTORY
############################
@bp.route("/physical-inventory", methods=["GET", "POST"])
@superuser_required
def physical_inventory_import():
    if request.method == "POST":
        step = request.form.get("step") or "upload"
        if step == "mapping":
            import_token = request.form.get("import_token", "")
            if not import_token:
                flash("No import data found. Please upload the file again.", "danger")
                return redirect(url_for("inventory.physical_inventory_import"))

            csv_text = _load_import_csv("physical_inventory", import_token)
            if csv_text is None:
                flash(
                    "Could not read the uploaded file data. Please upload the file again.",
                    "danger",
                )
                return redirect(url_for("inventory.physical_inventory_import"))

            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded file does not contain a header row.", "danger")
                _remove_import_csv("physical_inventory", import_token)
                return redirect(url_for("inventory.physical_inventory_import"))

            rows = list(reader)
            headers = reader.fieldnames

            primary_upload_column = request.form.get("primary_upload_column", "")
            primary_item_field = request.form.get("primary_item_field", "")
            secondary_upload_column = request.form.get("secondary_upload_column") or None
            secondary_item_field = request.form.get("secondary_item_field") or None
            quantity_column = request.form.get("quantity_column", "")
            duplicate_strategy = request.form.get("duplicate_strategy", "sum")
            create_missing_items = bool(request.form.get("create_missing_items"))

            if secondary_upload_column and not secondary_item_field:
                flash(
                    "Select a secondary Item field to disambiguate duplicate matches.",
                    "danger",
                )
                secondary_upload_column = None
            if secondary_item_field and not secondary_upload_column:
                flash(
                    "Select an upload column for the secondary match field.",
                    "danger",
                )
                secondary_item_field = None

            missing_fields = []
            if not primary_upload_column:
                missing_fields.append("Upload column for Item Name")
            if not primary_item_field:
                missing_fields.append("Item DB field to match against")
            if not quantity_column:
                missing_fields.append("Quantity column")

            if missing_fields:
                flash(
                    "Missing required fields: " + ", ".join(missing_fields) + ".",
                    "danger",
                )
                selected_mappings = {
                    "item_name": primary_upload_column,
                    "description": secondary_upload_column or "",
                    "quantity": quantity_column,
                }
                context = _prepare_physical_inventory_mapping_context(
                    csv_text,
                    selected_mappings=selected_mappings,
                    token=import_token,
                )
                context.update(
                    {
                        "selected_primary_item_field": primary_item_field,
                        "selected_secondary_item_field": secondary_item_field,
                        "source_filename": request.form.get("source_filename", ""),
                        "create_missing_items": create_missing_items,
                        "options": {
                            "trim_whitespace": bool(
                                request.form.get("trim_whitespace")
                            ),
                            "case_insensitive": bool(
                                request.form.get("case_insensitive")
                            ),
                            "remove_spaces": bool(request.form.get("remove_spaces")),
                            "remove_dashes_underscores": bool(
                                request.form.get("remove_dashes_underscores")
                            ),
                        },
                        "duplicate_strategy": duplicate_strategy,
                    }
                )
                return render_template(
                    "inventory/physical_inventory_mapping.html", **context
                )

            allowed_fields = {field["name"] for field in get_item_text_fields()}
            if primary_item_field not in allowed_fields:
                flash("Selected Item field is not available for matching.", "danger")
                return redirect(url_for("inventory.physical_inventory_import"))
            if secondary_item_field and secondary_item_field not in allowed_fields:
                flash(
                    "Selected secondary Item field is not available for matching.",
                    "danger",
                )
                secondary_item_field = None

            if primary_upload_column not in headers or quantity_column not in headers:
                flash(
                    "Selected columns were not found in the uploaded file. Please try again.",
                    "danger",
                )
                return redirect(url_for("inventory.physical_inventory_import"))

            options = NormalizationOptions(
                trim_whitespace=bool(request.form.get("trim_whitespace", True)),
                case_insensitive=bool(request.form.get("case_insensitive", True)),
                remove_spaces=bool(request.form.get("remove_spaces")),
                remove_dashes_underscores=bool(
                    request.form.get("remove_dashes_underscores")
                ),
            )

            match_results = match_upload_rows(
                rows,
                primary_upload_column=primary_upload_column,
                primary_item_field=primary_item_field,
                quantity_column=quantity_column,
                secondary_upload_column=secondary_upload_column,
                secondary_item_field=secondary_item_field,
                options=options,
            )

            created_items = 0
            if create_missing_items:
                candidates = build_missing_item_candidates(
                    match_results["unmatched_rows"],
                    primary_upload_column=primary_upload_column,
                    secondary_upload_column=secondary_upload_column,
                    secondary_item_field=secondary_item_field,
                    options=options,
                )
                if candidates:
                    try:
                        created_items = _create_missing_inventory_items(
                            candidates,
                            primary_item_field=primary_item_field,
                            options=options,
                            request_ip=request.remote_addr,
                        )
                        db.session.flush()
                    except SQLAlchemyError:
                        db.session.rollback()
                        flash(
                            "Unable to create missing items. No changes were applied.",
                            "danger",
                        )
                        return redirect(url_for("inventory.physical_inventory_import"))
                    match_results = match_upload_rows(
                        rows,
                        primary_upload_column=primary_upload_column,
                        primary_item_field=primary_item_field,
                        quantity_column=quantity_column,
                        secondary_upload_column=secondary_upload_column,
                        secondary_item_field=secondary_item_field,
                        options=options,
                    )

            if duplicate_strategy not in PHYSICAL_INVENTORY_DUPLICATE_STRATEGIES:
                duplicate_strategy = "sum"

            totals = aggregate_matched_rows(
                match_results["matched_rows"], duplicate_strategy
            )

            snapshot = PhysicalInventorySnapshot(
                created_by_user_id=current_user.id if current_user else None,
                source_filename=request.form.get("source_filename") or None,
                primary_upload_column=primary_upload_column,
                primary_item_field=primary_item_field,
                secondary_upload_column=secondary_upload_column,
                secondary_item_field=secondary_item_field,
                quantity_column=quantity_column,
                normalization_options=options.to_dict(),
                duplicate_strategy=duplicate_strategy,
                total_rows=match_results["total_rows"],
                matched_rows=match_results["matched_count"],
                unmatched_rows=match_results["unmatched_count"],
                ambiguous_rows=match_results["ambiguous_count"],
                created_items=created_items,
                unmatched_details=match_results["unmatched_rows"],
                ambiguous_details=match_results["ambiguous_rows"],
            )
            db.session.add(snapshot)
            db.session.flush()

            for item_id, quantity in totals.items():
                line = PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id,
                    item_id=item_id,
                    erp_quantity=quantity,
                )
                db.session.add(line)

            db.session.commit()
            _remove_import_csv("physical_inventory", import_token)

            flash(
                "Physical inventory snapshot created. "
                f"Matched {match_results['matched_count']} rows. "
                f"{created_items} items created; "
                f"{len(totals)} snapshot lines created; "
                f"{match_results['unmatched_count']} still unmatched.",
                "success",
            )
            return redirect(
                url_for("inventory.physical_inventory_snapshot", snapshot_id=snapshot.id)
            )

        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file uploaded.", "danger")
            return redirect(request.url)

        try:
            csv_text = parse_tabular_upload(file)
        except TabularImportError as exc:
            flash(str(exc), "danger")
            return redirect(request.url)

        headers, _ = preview_csv_text(csv_text)
        if not headers:
            flash("Uploaded file does not contain a header row.", "danger")
            return redirect(request.url)

        auto_mappings = _auto_map_physical_inventory_headers(headers)
        context = _prepare_physical_inventory_mapping_context(
            csv_text, selected_mappings=auto_mappings
        )
        context.update(
            {
                "selected_primary_item_field": "name",
                "selected_secondary_item_field": "description",
                "source_filename": file.filename,
                "create_missing_items": False,
                "options": {
                    "trim_whitespace": True,
                    "case_insensitive": True,
                    "remove_spaces": False,
                    "remove_dashes_underscores": False,
                },
                "duplicate_strategy": "sum",
            }
        )
        if not context.get("import_token"):
            flash(
                "Could not prepare the uploaded data. Please try again.",
                "danger",
            )
            return redirect(request.url)

        return render_template("inventory/physical_inventory_mapping.html", **context)

    return render_template("inventory/physical_inventory_upload.html")


@bp.route("/physical-inventory/test-matching", methods=["POST"])
@superuser_required
def physical_inventory_test_matching():
    payload = request.get_json(silent=True) or {}
    import_token = payload.get("import_token") or ""
    csv_text = _load_import_csv("physical_inventory", import_token)
    if csv_text is None:
        return jsonify({"error": "No import data found."}), 400

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return jsonify({"error": "Uploaded file does not contain a header row."}), 400

    rows = list(reader)

    primary_upload_column = payload.get("primary_upload_column") or ""
    primary_item_field = payload.get("primary_item_field") or ""
    quantity_column = payload.get("quantity_column") or ""
    secondary_upload_column = payload.get("secondary_upload_column") or None
    secondary_item_field = payload.get("secondary_item_field") or None

    if not primary_upload_column or not primary_item_field or not quantity_column:
        return jsonify({"error": "Missing required mapping selections."}), 400

    options = NormalizationOptions(
        trim_whitespace=bool(payload.get("trim_whitespace", True)),
        case_insensitive=bool(payload.get("case_insensitive", True)),
        remove_spaces=bool(payload.get("remove_spaces")),
        remove_dashes_underscores=bool(payload.get("remove_dashes_underscores")),
    )

    try:
        match_results = match_upload_rows(
            rows,
            primary_upload_column=primary_upload_column,
            primary_item_field=primary_item_field,
            quantity_column=quantity_column,
            secondary_upload_column=secondary_upload_column,
            secondary_item_field=secondary_item_field,
            options=options,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    unmatched_rows = match_results["unmatched_rows"]
    missing_value_rows = [
        entry
        for entry in unmatched_rows
        if entry.get("reason") == "Missing primary match value"
    ]
    missing_item_rows = [
        entry
        for entry in unmatched_rows
        if entry.get("reason") == "No match found"
    ]

    missing_item_candidates = build_missing_item_candidates(
        missing_item_rows,
        primary_upload_column=primary_upload_column,
        secondary_upload_column=secondary_upload_column,
        secondary_item_field=secondary_item_field,
        options=options,
    )
    missing_item_preview = []
    secondary_field_label = None
    if secondary_item_field and secondary_item_field != "description":
        secondary_field_label = f"Item.{secondary_item_field}"
    for candidate in missing_item_candidates[:10]:
        fields = candidate.get("fields") or {}
        missing_item_preview.append(
            {
                "name": candidate.get("name", ""),
                "description": fields.get("description", ""),
                "secondary_field_label": secondary_field_label,
                "secondary_field_value": (
                    fields.get(secondary_item_field) if secondary_item_field else ""
                ),
            }
        )

    return jsonify(
        {
            "match_rate": match_results["match_rate"],
            "matched_count": match_results["matched_count"],
            "unmatched_count": match_results["unmatched_count"],
            "ambiguous_count": match_results["ambiguous_count"],
            "missing_value_rows": missing_value_rows[:5],
            "missing_item_rows": missing_item_rows[:5],
            "ambiguous_rows": match_results["ambiguous_rows"][:5],
            "missing_item_candidates": {
                "count": len(missing_item_candidates),
                "preview": missing_item_preview,
            },
            "fields_used": {
                "primary_upload_column": primary_upload_column,
                "primary_item_field": primary_item_field,
                "secondary_upload_column": secondary_upload_column,
                "secondary_item_field": secondary_item_field,
                "quantity_column": quantity_column,
            },
        }
    )


@bp.route("/physical-inventory/field-samples")
@superuser_required
def physical_inventory_field_samples():
    field_name = request.args.get("field", "")
    samples = get_item_field_samples(field_name)
    return jsonify({"field": field_name, "samples": samples})


@bp.route("/physical-inventory/snapshots")
@superuser_required
def physical_inventory_snapshots():
    snapshots = (
        PhysicalInventorySnapshot.query.order_by(PhysicalInventorySnapshot.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template(
        "inventory/physical_inventory_snapshots.html", snapshots=snapshots
    )


@bp.route("/physical-inventory/<int:snapshot_id>")
@superuser_required
def physical_inventory_snapshot(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(joinedload(PhysicalInventorySnapshotLine.item))
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )
    return render_template(
        "inventory/physical_inventory_snapshot.html",
        snapshot=snapshot,
        lines=lines,
    )


@bp.route("/physical-inventory/<int:snapshot_id>/counts", methods=["GET", "POST"])
@superuser_required
def physical_inventory_counts(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )

    if request.method == "POST":
        for line in lines:
            value = request.form.get(f"counted_{line.id}")
            if value is None or str(value).strip() == "":
                line.counted_quantity = None
                continue
            parsed = _parse_decimal(value)
            line.counted_quantity = parsed if parsed is not None else None
        db.session.commit()
        flash("Counted quantities updated.", "success")
        return redirect(
            url_for("inventory.physical_inventory_counts", snapshot_id=snapshot_id)
        )

    return render_template(
        "inventory/physical_inventory_counts.html",
        snapshot=snapshot,
        lines=lines,
    )


@bp.route("/physical-inventory/<int:snapshot_id>/count-sheet")
@superuser_required
def physical_inventory_count_sheet(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )
    rows = _build_physical_inventory_count_rows(lines)
    return render_template(
        "inventory/physical_inventory_count_sheet.html",
        snapshot=snapshot,
        rows=rows,
    )


@bp.route("/physical-inventory/<int:snapshot_id>/count-sheet.csv")
@superuser_required
def physical_inventory_count_sheet_export(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )
    rows = _build_physical_inventory_count_rows(lines)
    filename = f"count_sheet_snapshot_{snapshot.id}.csv"
    columns = [
        ("aisle", "Aisle"),
        ("location_code", "Location Code"),
        ("location_description", "Location Description"),
        ("item_name", "Item Name"),
        ("item_description", "Item Description"),
        ("sku", "SKU"),
        ("erp_quantity", "ERP Total Qty"),
        ("counted_quantity", "Counted Qty"),
        ("notes", "Notes"),
    ]
    return export_rows_to_csv(rows, columns, filename)


@bp.route("/physical-inventory/<int:snapshot_id>/count-sheet/aisle")
@superuser_required
def physical_inventory_count_sheet_by_aisle(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )
    rows = _build_physical_inventory_count_rows(lines)
    aisles = sorted({row["aisle"] for row in rows}, key=_aisle_sort_key)
    selected_aisle = request.args.get("aisle") or (aisles[0] if aisles else None)
    aisle_rows = [row for row in rows if row["aisle"] == selected_aisle]
    return render_template(
        "inventory/physical_inventory_count_sheet_by_aisle.html",
        snapshot=snapshot,
        aisles=aisles,
        selected_aisle=selected_aisle,
        rows=aisle_rows,
        today=date.today(),
    )


@bp.route("/physical-inventory/<int:snapshot_id>/count-sheet-aisles.zip")
@superuser_required
def physical_inventory_count_sheet_export_by_aisle(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )
    rows = _build_physical_inventory_count_rows(lines)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["aisle"])].append(row)

    columns = [
        ("aisle", "Aisle"),
        ("location_code", "Location Code"),
        ("location_description", "Location Description"),
        ("item_name", "Item Name"),
        ("item_description", "Item Description"),
        ("sku", "SKU"),
        ("erp_quantity", "ERP Total Qty"),
        ("counted_quantity", "Counted Qty"),
        ("notes", "Notes"),
    ]

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for aisle, aisle_rows in sorted(grouped.items(), key=lambda item: _aisle_sort_key(item[0])):
            safe_aisle = re.sub(r"[^A-Za-z0-9_-]+", "_", aisle) or "UNASSIGNED"
            filename = f"count_sheet_snapshot_{snapshot.id}_aisle_{safe_aisle}.csv"
            csv_output = io.StringIO()
            writer = csv.writer(csv_output)
            writer.writerow([header for _, header in columns])
            for row in aisle_rows:
                writer.writerow([row.get(field, "") for field, _ in columns])
            zip_file.writestr(filename, csv_output.getvalue())

    response = Response(output.getvalue(), mimetype="application/zip")
    response.headers[
        "Content-Disposition"
    ] = f"attachment; filename=count_sheet_snapshot_{snapshot.id}_aisles.zip"
    return response


@bp.route("/physical-inventory/<int:snapshot_id>/reconciliation")
@superuser_required
def physical_inventory_reconciliation(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    lines = (
        PhysicalInventorySnapshotLine.query.options(
            joinedload(PhysicalInventorySnapshotLine.item).joinedload(Item.default_location)
        )
        .filter_by(snapshot_id=snapshot_id)
        .all()
    )

    reconciliation_rows = []
    for line in lines:
        counted = line.counted_quantity
        erp = line.erp_quantity or Decimal(0)
        variance = None
        status = "UNCOUNTED"
        if line.item and line.item.default_location is None:
            status = "UNLOCATED"
        if counted is not None:
            variance = Decimal(counted) - Decimal(erp)
            if variance == 0:
                status = "MATCH"
            elif variance > 0:
                status = "OVER"
            else:
                status = "SHORT"
        reconciliation_rows.append(
            {
                "item": line.item,
                "erp": erp,
                "counted": counted,
                "variance": variance,
                "status": status,
            }
        )

    return render_template(
        "inventory/physical_inventory_reconciliation.html",
        snapshot=snapshot,
        reconciliation_rows=reconciliation_rows,
    )


@bp.route("/physical-inventory/<int:snapshot_id>/unmatched.csv")
@superuser_required
def physical_inventory_unmatched_export(snapshot_id: int):
    snapshot = PhysicalInventorySnapshot.query.get_or_404(snapshot_id)
    rows = snapshot.unmatched_details or []
    ambiguous_rows = snapshot.ambiguous_details or []
    combined = rows + ambiguous_rows
    if not combined:
        return Response("", mimetype="text/csv")

    headers = sorted(
        {
            key
            for entry in combined
            for key in (entry.get("row") or {}).keys()
        }
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["reason", "row_index", *headers])
    for entry in combined:
        row = entry.get("row") or {}
        writer.writerow(
            [entry.get("reason", ""), entry.get("row_index", ""), *[row.get(h, "") for h in headers]]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=unmatched_rows.csv"},
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
        item_id=item.id,
        item_number=item.sku,
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


@bp.get("/api/locations/search")
def search_locations_api():
    """Search locations for typeahead dropdowns."""

    query = (request.args.get("q") or "").strip()
    base_query = Location.query.options(load_only(Location.id, Location.code, Location.description))
    unassigned = Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).one_or_none()

    if not query:
        results = []
        if unassigned:
            results.append(unassigned)
        remaining_limit = LOCATION_SEARCH_LIMIT - len(results)
        if remaining_limit > 0:
            matches = base_query
            if unassigned:
                matches = matches.filter(Location.id != unassigned.id)
            matches = (
                matches.order_by(func.lower(Location.code))
                .limit(remaining_limit)
                .all()
            )
            results.extend(matches)
        return jsonify(
            [
                {
                    "id": location.id,
                    "code": location.code,
                    "description": location.description or "",
                    "label": _format_location_label(location),
                }
                for location in results
            ]
        )

    lowered = query.lower()
    contains_pattern = f"%{lowered}%"
    prefix_pattern = f"{lowered}%"
    ranking = case(
        (func.lower(Location.code).like(prefix_pattern), 0),
        else_=1,
    )

    matches = (
        base_query.filter(
            or_(
                func.lower(Location.code).like(contains_pattern),
                func.lower(Location.description).like(contains_pattern),
            )
        )
        .order_by(ranking, func.lower(Location.code))
        .limit(LOCATION_SEARCH_LIMIT)
        .all()
    )

    return jsonify(
        [
            {
                "id": location.id,
                "code": location.code,
                "description": location.description or "",
                "label": _format_location_label(location),
            }
            for location in matches
        ]
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
    batches = (
        Batch.active().options(load_only(Batch.id, Batch.lot_number))
        .all()
    )

    if request.method == "POST":
        sku = request.form["sku"].strip()
        batch_id = int(request.form["batch_id"])
        location_id = int(request.form["location_id"])
        counted_qty = int(request.form["counted_qty"])
        person = request.form["person"].strip()
        reference = request.form.get("reference", "Cycle Count")

        item = Item.query.filter_by(sku=sku).first()
        batch = Batch.active().filter_by(id=batch_id).first()
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

        location_errors = []

        def resolve_location_choice(label: str, field_name: str):
            raw_value = request.form.get(field_name)
            if raw_value is None or not str(raw_value).strip():
                return None
            location_id = _parse_location_id(raw_value)
            if location_id is None:
                location_errors.append(f"{label} selection is invalid.")
                return None
            location = Location.query.get(location_id)
            if location is None:
                location_errors.append(f"{label} not found.")
            return location

        default_location = resolve_location_choice("Primary location", "default_location_id")
        secondary_location = resolve_location_choice(
            "Secondary location",
            "secondary_location_id",
        )
        point_of_use_location = resolve_location_choice(
            "Point-of-use location",
            "point_of_use_location_id",
        )

        location_errors.extend(
            _validate_item_location_duplicates(
                default_location.id if default_location else None,
                secondary_location.id if secondary_location else None,
                point_of_use_location.id if point_of_use_location else None,
            )
        )

        if location_errors:
            for error in location_errors:
                flash(error, "danger")
            return render_template(
                "inventory/add_item.html", next_sku=next_sku, locations=locations
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
            secondary_location=secondary_location,
            point_of_use_location=point_of_use_location,
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

            location_errors = []

            def resolve_location_choice(label: str, field_name: str):
                raw_value = request.form.get(field_name)
                if raw_value is None or not str(raw_value).strip():
                    return None
                location_id = _parse_location_id(raw_value)
                if location_id is None:
                    location_errors.append(f"{label} selection is invalid.")
                    return None
                location = Location.query.get(location_id)
                if location is None:
                    location_errors.append(f"{label} not found.")
                return location

            default_location = resolve_location_choice("Primary location", "default_location_id")
            secondary_location = resolve_location_choice(
                "Secondary location",
                "secondary_location_id",
            )
            point_of_use_location = resolve_location_choice(
                "Point-of-use location",
                "point_of_use_location_id",
            )

            location_errors.extend(
                _validate_item_location_duplicates(
                    default_location.id if default_location else None,
                    secondary_location.id if secondary_location else None,
                    point_of_use_location.id if point_of_use_location else None,
                )
            )

            if location_errors:
                for error in location_errors:
                    flash(error, "danger")
                return render_template(
                    "inventory/edit_item.html", item=item, locations=locations
                )

            item.default_location_id = default_location.id if default_location else None
            item.secondary_location_id = (
                secondary_location.id if secondary_location else None
            )
            item.point_of_use_location_id = (
                point_of_use_location.id if point_of_use_location else None
            )

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

            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded CSV does not contain a header row.", "danger")

                _remove_import_csv("items", import_token)

                return redirect(url_for("inventory.import_items"))

            auto_mappings = resolve_import_mappings(
                reader.fieldnames, ITEM_IMPORT_FIELDS, ITEMS_HEADER_ALIASES
            )
            selected_mappings = {}
            for field_cfg in ITEM_IMPORT_FIELDS:
                selected_header = request.form.get(f"mapping_{field_cfg['field']}", "")
                if selected_header:
                    selected_mappings[field_cfg["field"]] = selected_header
                elif field_cfg["field"] in auto_mappings:
                    selected_mappings[field_cfg["field"]] = auto_mappings[field_cfg["field"]]

            missing_required = [
                field_cfg["label"]
                for field_cfg in ITEM_IMPORT_FIELDS
                if field_cfg["required"] and field_cfg["field"] not in selected_mappings
            ]
            if missing_required:
                expected = ", ".join(expected_headers(ITEMS_CSV_COLUMNS))
                flash(
                    "Missing required columns: "
                    + ", ".join(missing_required)
                    + f". Expected headers include: {expected}.",
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


            _log_unmapped_headers("items", reader.fieldnames, selected_mappings)

            next_sku = _next_auto_sku_value()

            def extract(row, field):
                header = selected_mappings.get(field)
                if not header:
                    return ""
                value = row.get(header)
                return value if value is not None else ""

            locations_by_id = {loc.id: loc for loc in Location.query.all()}
            locations_by_code = {loc.code: loc for loc in locations_by_id.values()}

            count_new, count_updated = 0, 0
            errors = []
            for row in reader:
                item_id = _parse_int(extract(row, "id"))
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

                row_errors = []

                def resolve_location_from_row(prefix: str):
                    location_id = _parse_int(extract(row, f"{prefix}_id"))
                    location_code = extract(row, f"{prefix}_code").strip()
                    location = None
                    if location_id is not None:
                        location = locations_by_id.get(location_id)
                    if location is None and location_code:
                        location = locations_by_code.get(location_code)
                    if (location_id is not None or location_code) and location is None:
                        row_errors.append(
                            f"Unknown {prefix.replace('_', ' ')} for SKU {sku or '(auto)'}."
                        )
                    return location

                default_location = resolve_location_from_row("default_location")
                secondary_location = resolve_location_from_row("secondary_location")
                point_of_use_location = resolve_location_from_row("point_of_use_location")

                row_errors.extend(
                    _validate_item_location_duplicates(
                        default_location.id if default_location else None,
                        secondary_location.id if secondary_location else None,
                        point_of_use_location.id if point_of_use_location else None,
                    )
                )

                if row_errors:
                    errors.extend(row_errors)
                    continue

                existing = None
                if item_id is not None:
                    existing = Item.query.get(item_id)
                if existing is None and sku:
                    existing = Item.query.filter_by(sku=sku).first()
                if existing:
                    if name:
                        existing.name = name
                    if "unit" in selected_mappings:
                        existing.unit = unit
                    if "description" in selected_mappings:
                        existing.description = description or None
                    if "min_stock" in selected_mappings:
                        existing.min_stock = min_stock
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
                    if (
                        "default_location_id" in selected_mappings
                        or "default_location_code" in selected_mappings
                    ):
                        existing.default_location_id = (
                            default_location.id if default_location else None
                        )
                    if (
                        "secondary_location_id" in selected_mappings
                        or "secondary_location_code" in selected_mappings
                    ):
                        existing.secondary_location_id = (
                            secondary_location.id if secondary_location else None
                        )
                    if (
                        "point_of_use_location_id" in selected_mappings
                        or "point_of_use_location_code" in selected_mappings
                    ):
                        existing.point_of_use_location_id = (
                            point_of_use_location.id if point_of_use_location else None
                        )
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
                        description=description or None,
                        min_stock=min_stock,
                        notes=notes_value if has_notes_column else None,
                        list_price=list_price_value if has_list_price_column else None,
                        last_unit_cost=(
                            last_unit_cost_value if has_last_unit_cost_column else None
                        ),
                        item_class=(
                            (item_class_value or None) if has_item_class_column else None
                        ),
                        default_location_id=(
                            default_location.id if default_location else None
                        ),
                        secondary_location_id=(
                            secondary_location.id if secondary_location else None
                        ),
                        point_of_use_location_id=(
                            point_of_use_location.id if point_of_use_location else None
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
            if errors:
                preview = "; ".join(errors[:5])
                extra = "" if len(errors) <= 5 else f" (and {len(errors) - 5} more)"
                flash(
                    f"Skipped {len(errors)} rows due to location validation errors: {preview}{extra}",
                    "warning",
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


        headers = next(csv.reader(io.StringIO(csv_text)), [])
        auto_mappings = resolve_import_mappings(
            headers, ITEM_IMPORT_FIELDS, ITEMS_HEADER_ALIASES
        )
        context = _prepare_item_import_mapping_context(
            csv_text, selected_mappings=auto_mappings
        )
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
    Export items to CSV.
    """
    items = Item.query.options(
        joinedload(Item.default_location),
        joinedload(Item.secondary_location),
        joinedload(Item.point_of_use_location),
    ).order_by(Item.sku).all()

    def iter_rows():
        for item in items:
            default_location = item.default_location
            secondary_location = item.secondary_location
            point_of_use_location = item.point_of_use_location
            yield {
                "id": item.id,
                "sku": item.sku,
                "name": item.name,
                "type": item.type,
                "unit": item.unit,
                "description": item.description,
                "min_stock": item.min_stock,
                "notes": item.notes,
                "list_price": item.list_price,
                "last_unit_cost": item.last_unit_cost,
                "item_class": item.item_class,
                "default_location_id": (
                    default_location.id if default_location else None
                ),
                "default_location_code": (
                    default_location.code if default_location else None
                ),
                "secondary_location_id": (
                    secondary_location.id if secondary_location else None
                ),
                "secondary_location_code": (
                    secondary_location.code if secondary_location else None
                ),
                "point_of_use_location_id": (
                    point_of_use_location.id if point_of_use_location else None
                ),
                "point_of_use_location_code": (
                    point_of_use_location.code if point_of_use_location else None
                ),
            }

    filename = f"items_export_{date.today().isoformat()}.csv"
    return export_rows_to_csv(iter_rows(), ITEMS_CSV_COLUMNS, filename)


############################
# LOCATION ROUTES
############################
@bp.route("/locations")
def list_locations():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    search = (request.args.get("search") or "").strip()
    row_filter_raw = (request.args.get("row") or "").strip()
    row_filter = row_filter_raw.upper() or None
    description_query = (request.args.get("q") or "").strip()
    sort_param = (request.args.get("sort") or "code").strip().lower()
    sort_dir = (request.args.get("dir") or "asc").strip().lower()
    if sort_param not in {"code", "row", "description", "level", "bay"}:
        sort_param = "code"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "asc"
    like_pattern = f"%{search}%" if search else None
    description_pattern = f"%{description_query}%" if description_query else None

    available_rows_query = Location.query.with_entities(Location.code).all()
    available_rows = sorted(
        {
            parsed.row
            for (code,) in available_rows_query
            if (parsed := parse_location_code(code)).row
        }
    )

    locations_query = Location.query
    if like_pattern:
        matching_location_ids = (
            db.session.query(Movement.location_id)
            .join(Item, Item.id == Movement.item_id)
            .group_by(Movement.location_id, Movement.item_id)
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

    if description_pattern:
        locations_query = locations_query.filter(
            Location.description.ilike(description_pattern)
        )

    locations = locations_query.all()
    parsed_by_location = {
        location.id: parse_location_code(location.code) for location in locations
    }

    if row_filter:
        locations = [
            location
            for location in locations
            if parsed_by_location.get(location.id).row == row_filter
        ]

    def natural_code_key(location: Location) -> tuple:
        parsed = parsed_by_location.get(location.id)
        if not parsed or parsed.level is None or parsed.row is None or parsed.bay is None:
            return (math.inf, "", math.inf, (location.code or "").lower())
        return (parsed.level, parsed.row, parsed.bay, (location.code or "").lower())

    def row_sort_key(location: Location) -> tuple:
        parsed = parsed_by_location.get(location.id)
        row = parsed.row if parsed else None
        level = parsed.level if parsed else None
        bay = parsed.bay if parsed else None
        return (
            row is None,
            row or "",
            level is None,
            level or 0,
            bay is None,
            bay or 0,
            (location.code or "").lower(),
        )

    def description_sort_key(location: Location) -> tuple:
        description = (location.description or "").lower()
        return (description, natural_code_key(location))

    def level_sort_key(location: Location) -> tuple:
        parsed = parsed_by_location.get(location.id)
        level = parsed.level if parsed else None
        return (
            level is None,
            level or 0,
            parsed.row if parsed else "",
            parsed.bay if parsed else 0,
            (location.code or "").lower(),
        )

    def bay_sort_key(location: Location) -> tuple:
        parsed = parsed_by_location.get(location.id)
        bay = parsed.bay if parsed else None
        return (
            bay is None,
            bay or 0,
            parsed.row if parsed else "",
            parsed.level if parsed else 0,
            (location.code or "").lower(),
        )

    sort_key_map = {
        "code": natural_code_key,
        "row": row_sort_key,
        "description": description_sort_key,
        "level": level_sort_key,
        "bay": bay_sort_key,
    }
    locations.sort(key=sort_key_map[sort_param], reverse=sort_dir == "desc")

    total_locations = len(locations)
    size = max(size, 1)
    pages = max(1, math.ceil(total_locations / size)) if total_locations else 1
    page = min(max(page, 1), pages)
    start = (page - 1) * size
    end = start + size
    page_locations = locations[start:end]

    balances_query = (
        db.session.query(
            Movement.location_id,
            Movement.item_id,
            func.sum(Movement.quantity).label("on_hand"),
            func.max(pending_receipt_case()).label("pending_qty"),
        )
        .join(Item, Item.id == Movement.item_id)
        .join(Location, Location.id == Movement.location_id)
        .group_by(Movement.location_id, Movement.item_id)
    )

    location_ids = [location.id for location in page_locations]
    if location_ids:
        balances_query = balances_query.filter(Movement.location_id.in_(location_ids))

    if like_pattern:
        balances_query = balances_query.filter(
            or_(
                Item.sku.ilike(like_pattern),
                Item.name.ilike(like_pattern),
                Location.code.ilike(like_pattern),
                Location.description.ilike(like_pattern),
            )
        )

    if location_ids:
        balances = balances_query.all()
    else:
        balances = []
    item_ids = {item_id for _, item_id, _, _ in balances}
    items = (
        {i.id: i for i in Item.query.filter(Item.id.in_(item_ids)).all()}
        if item_ids
        else {}
    )

    balances_by_location: dict[int, list[dict[str, Union[Item, int, bool]]]] = defaultdict(list)
    pending_count_by_location: dict[int, int] = defaultdict(int)
    for location_id, item_id, on_hand, pending_qty in balances:
        on_hand_value = int(on_hand or 0)
        is_pending = bool(pending_qty)
        if on_hand_value == 0 and not is_pending:
            continue
        item = items.get(item_id)
        if not item:
            continue
        balances_by_location[location_id].append(
            {
                "item": item,
                "quantity": on_hand_value,
                "pending_qty": is_pending,
            }
        )
        if is_pending:
            pending_count_by_location[location_id] += 1

    for location_balances in balances_by_location.values():
        location_balances.sort(key=lambda entry: entry["item"].sku)

    return render_template(
        "inventory/list_locations.html",
        locations=page_locations,
        page=page,
        size=size,
        pages=pages,
        search=search,
        row_filter=row_filter or "",
        description_query=description_query,
        sort=sort_param,
        sort_dir=sort_dir,
        available_rows=available_rows,
        total_locations=total_locations,
        query_params={
            "search": search or None,
            "row": row_filter or None,
            "q": description_query or None,
            "sort": sort_param,
            "dir": sort_dir,
            "size": size,
        },
        balances_by_location=balances_by_location,
        pending_count_by_location=pending_count_by_location,
    )


@bp.route("/locations/delete-all", methods=["POST"])
@superuser_required
def delete_all_locations():
    if request.form.get("confirm_delete") != "DELETE":
        flash("Type DELETE to confirm deleting all locations.", "warning")
        return redirect(url_for("inventory.list_locations"))

    has_movements = db.session.query(Movement.id).limit(1).first() is not None
    if has_movements:
        flash(
            "Cannot delete all locations because inventory movements reference them. "
            "Remove stock records before trying again.",
            "danger",
        )
        return redirect(url_for("inventory.list_locations"))

    items_cleared = 0
    deleted = 0
    try:
        items_cleared = (
            db.session.query(Item)
            .filter(
                or_(
                    Item.default_location_id.isnot(None),
                    Item.secondary_location_id.isnot(None),
                    Item.point_of_use_location_id.isnot(None),
                )
            )
            .update(
                {
                    Item.default_location_id: None,
                    Item.secondary_location_id: None,
                    Item.point_of_use_location_id: None,
                },
                synchronize_session=False,
            )
        )
        deleted = Location.query.delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete all locations.")
        flash("Failed to delete all locations. Please try again.", "danger")
        return redirect(url_for("inventory.list_locations"))

    if deleted:
        label = "location" if deleted == 1 else "locations"
        item_label = "item" if items_cleared == 1 else "items"
        flash(
            f"Cleared location references on {items_cleared} {item_label} and deleted "
            f"{deleted} {label}.",
            "success",
        )
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
@login_required
def edit_location(location_id):
    location = Location.query.get_or_404(location_id)
    can_edit_location = current_user.is_authenticated and (
        current_user.has_any_role(("admin",)) or is_superuser()
    )
    can_remove = can_edit_location
    can_set_pending = current_user.is_authenticated
    can_move_pending = current_user.is_authenticated

    all_locations = Location.query.order_by(Location.code).all()

    def load_location_lines():
        return get_location_inventory_lines(location_id, include_pending=True)

    if request.method == "POST" and not can_edit_location:
        abort(403)

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
                inventory_lines=load_location_lines(),
                can_remove=can_remove,
                can_edit_location=can_edit_location,
                can_set_pending=can_set_pending,
                can_move_pending=can_move_pending,
                all_locations=all_locations,
                remove_reasons=_get_remove_reasons(),
                next_url=url_for("inventory.edit_location", location_id=location_id),
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
        inventory_lines=load_location_lines(),
        can_remove=can_remove,
        can_edit_location=can_edit_location,
        can_set_pending=can_set_pending,
        can_move_pending=can_move_pending,
        all_locations=all_locations,
        remove_reasons=_get_remove_reasons(),
        next_url=url_for("inventory.edit_location", location_id=location_id),
    )


@bp.post("/location/<int:location_id>/remove-all-items")
@require_admin_or_superuser
def remove_all_items_from_location(location_id: int):
    location = Location.query.get_or_404(location_id)
    confirmation = (request.form.get("confirmation") or "").strip()
    reason = (request.form.get("reason") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    if confirmation != location.code:
        flash("Location confirmation does not match.", "danger")
        return redirect(url_for("inventory.edit_location", location_id=location_id))

    remove_reasons = _get_remove_reasons()
    if not reason or (remove_reasons and reason not in remove_reasons):
        flash("Select a valid removal reason.", "danger")
        return redirect(url_for("inventory.edit_location", location_id=location_id))

    lines = get_location_inventory_lines(location_id, include_pending=True)
    removal_lines = [
        line
        for line in lines
        if Decimal(str(line.get("on_hand", 0))) > 0 or line.get("pending_qty")
    ]
    if not removal_lines:
        flash("No stock available to remove at this location.", "info")
        return redirect(url_for("inventory.edit_location", location_id=location_id))

    reference = reason if not notes else f"{reason} - {notes}"
    total_lines = 0
    total_qty = Decimal("0")
    with db.session.begin_nested():
        for line in removal_lines:
            qty = Decimal(str(line.get("on_hand", 0)))
            is_pending = bool(line.get("pending_qty"))
            if qty <= 0 and not is_pending:
                continue
            if is_pending and line.get("pending_receipt_id"):
                pending_movement = Movement.query.get(line["pending_receipt_id"])
                if _is_pending_receipt(pending_movement):
                    pending_movement.reference = _resolve_pending_reference(
                        pending_movement.reference, "voided"
                    )
            db.session.add(
                Movement(
                    item_id=line["item_id"],
                    batch_id=line.get("batch_id"),
                    location_id=location_id,
                    quantity=-qty if qty > 0 else Decimal("0"),
                    movement_type="REMOVE_FROM_LOCATION",
                    person=_movement_person(),
                    reference=reference,
                )
            )
            total_lines += 1
            if qty > 0:
                total_qty += qty
    db.session.commit()

    flash(
        f"Removed {total_qty} units across {total_lines} items/lots from {location.code}.",
        "success",
    )
    return redirect(url_for("inventory.edit_location", location_id=location_id))


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
def _parse_stock_quantity(value: str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        value = str(value)
    value = value.strip()
    if not value:
        return None
    try:
        return Decimal(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _get_location_on_hand(item_id: int, location_id: int) -> Decimal:
    total = (
        db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(Movement.item_id == item_id, Movement.location_id == location_id)
        .scalar()
    )
    return Decimal(total or 0)


def _get_location_on_hand_by_batch(
    item_id: int, location_id: int, batch_id: int | None
) -> Decimal:
    filters = [Movement.item_id == item_id, Movement.location_id == location_id]
    if batch_id is None:
        filters.append(Movement.batch_id.is_(None))
    else:
        filters.append(Movement.batch_id == batch_id)

    total = (
        db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(*filters)
        .scalar()
    )
    return Decimal(total or 0)


def _get_remove_reasons() -> list[str]:
    reasons = current_app.config.get("INVENTORY_REMOVE_REASONS", [])
    if isinstance(reasons, str):
        reasons = [reason.strip() for reason in reasons.split(",")]
    return [reason for reason in reasons if reason]


def _get_item_location_batch_balances(item_id: int) -> dict[int, list[dict[str, object]]]:
    rows = (
        db.session.query(
            Movement.location_id,
            Movement.batch_id,
            Batch.lot_number,
            func.coalesce(func.sum(Movement.quantity), 0).label("on_hand"),
        )
        .outerjoin(Batch, Batch.id == Movement.batch_id)
        .filter(Movement.item_id == item_id)
        .filter(or_(Movement.batch_id.is_(None), Batch.removed_at.is_(None)))
        .group_by(Movement.location_id, Movement.batch_id, Batch.lot_number)
        .all()
    )

    balances: dict[int, list[dict[str, object]]] = defaultdict(list)
    for location_id, batch_id, lot_number, on_hand in rows:
        qty = Decimal(on_hand or 0)
        if qty == 0:
            continue
        balances[location_id].append(
            {
                "batch_id": batch_id,
                "lot_number": lot_number or "",
                "quantity": qty,
            }
        )
    return balances


def _is_pending_receipt(movement: Movement | None) -> bool:
    if movement is None:
        return False
    reference = (movement.reference or "").lower()
    return (
        movement.movement_type == "RECEIPT"
        and (movement.quantity or 0) == 0
        and PENDING_RECEIPT_MARKER in reference
    )


def _resolve_pending_reference(reference: str | None, status: str) -> str:
    base = (reference or "Receipt").replace(" (quantity pending)", "")
    base = base.replace("quantity pending", "").strip()
    base = " ".join(base.split())
    return f"{base} (quantity {status})"


def _movement_person() -> str | None:
    if not current_user.is_authenticated:
        return None
    try:
        return current_user.username
    except DetachedInstanceError:
        try:
            user_id = int(session.get("_user_id"))
        except (TypeError, ValueError):
            return None
        user = User.query.get(user_id)
        return user.username if user else None


def _stock_overview_query():
    movement_agg = (
        db.session.query(
            Movement.item_id.label("item_id"),
            func.coalesce(func.sum(Movement.quantity), 0).label("total_qty"),
            func.count(func.distinct(Movement.location_id)).label("location_count"),
            func.max(Movement.date).label("last_updated"),
        )
        .group_by(Movement.item_id)
        .subquery()
    )

    batch_agg = (
        db.session.query(
            Batch.item_id.label("item_id"),
            func.count(Batch.id).label("batch_count"),
        )
        .filter(Batch.removed_at.is_(None))
        .group_by(Batch.item_id)
        .subquery()
    )

    primary_location = aliased(Location)
    secondary_location = aliased(Location)
    point_of_use_location = aliased(Location)

    total_qty = func.coalesce(movement_agg.c.total_qty, 0).label("total_qty")
    location_count = func.coalesce(
        movement_agg.c.location_count, 0
    ).label("location_count")
    batch_count = func.coalesce(batch_agg.c.batch_count, 0).label("batch_count")
    last_updated = movement_agg.c.last_updated.label("last_updated")

    overview_query = (
        db.session.query(
            Item,
            total_qty,
            location_count,
            batch_count,
            last_updated,
            primary_location,
            secondary_location,
            point_of_use_location,
        )
        .options(lazyload(Item.default_location))
        .outerjoin(movement_agg, movement_agg.c.item_id == Item.id)
        .outerjoin(batch_agg, batch_agg.c.item_id == Item.id)
        .outerjoin(primary_location, primary_location.id == Item.default_location_id)
        .outerjoin(secondary_location, secondary_location.id == Item.secondary_location_id)
        .outerjoin(
            point_of_use_location, point_of_use_location.id == Item.point_of_use_location_id
        )
    )
    return (
        overview_query,
        total_qty,
        location_count,
        batch_count,
        last_updated,
        primary_location,
        secondary_location,
        point_of_use_location,
    )


@bp.route("/stock")
def list_stock():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    status = request.args.get("status", "all")
    search = (request.args.get("q") or request.args.get("search") or "").strip()
    location_id = request.args.get("location_id", type=int)
    in_stock = request.args.get("in_stock") in {"1", "true", "on", "yes"}
    sort = request.args.get("sort", "sku")
    direction = request.args.get("dir", "asc")
    if status not in {"all", "low", "near"}:
        status = "all"

    (
        overview_query,
        total_qty,
        location_count,
        batch_count,
        last_updated,
        primary_location,
        secondary_location,
        point_of_use_location,
    ) = _stock_overview_query()

    if search:
        like_pattern = f"%{search}%"
        overview_query = overview_query.filter(
            or_(
                Item.sku.ilike(like_pattern),
                Item.name.ilike(like_pattern),
                Item.description.ilike(like_pattern),
            )
        )

    if location_id:
        location_items = (
            db.session.query(Movement.item_id)
            .filter(Movement.location_id == location_id)
            .distinct()
            .subquery()
        )
        overview_query = overview_query.filter(
            Item.id.in_(select(location_items.c.item_id))
        )

    if status in {"low", "near"}:
        multiplier = 1.05 if status == "low" else 1.25
        overview_query = overview_query.filter(
            total_qty < (func.coalesce(Item.min_stock, 0) * multiplier)
        )

    if in_stock:
        overview_query = overview_query.filter(total_qty > 0)

    sort_map = {
        "sku": Item.sku,
        "name": Item.name,
        "qty": total_qty,
        "locations": location_count,
        "primary": primary_location.code,
        "updated": last_updated,
        "min_stock": Item.min_stock,
    }
    sort_column = sort_map.get(sort, Item.sku)
    if direction not in {"asc", "desc"}:
        direction = "asc"
    order_func = desc if direction == "desc" else asc

    overview_query = overview_query.order_by(order_func(sort_column), Item.sku.asc())

    pagination = overview_query.paginate(page=page, per_page=size, error_out=False)

    entries = [
        {
            "item": item,
            "total_qty": float(total or 0),
            "location_count": location_count or 0,
            "batch_count": batch_count or 0,
            "last_updated": last_updated,
            "primary_location": primary_location,
            "secondary_location": secondary_location,
            "point_of_use_location": point_of_use_location,
        }
        for (
            item,
            total,
            location_count,
            batch_count,
            last_updated,
            primary_location,
            secondary_location,
            point_of_use_location,
        ) in pagination.items
    ]

    locations = Location.query.order_by(Location.code).all()

    def build_stock_url(**overrides):
        args = request.args.to_dict()
        args.update({key: value for key, value in overrides.items() if value is not None})
        cleaned = {
            key: value
            for key, value in args.items()
            if value not in ("", None)
        }
        return url_for("inventory.list_stock", **cleaned)

    return render_template(
        "inventory/list_stock.html",
        entries=entries,
        status=status,
        page=page,
        size=size,
        pages=pagination.pages,
        search=search,
        location_id=location_id,
        in_stock=in_stock,
        sort=sort,
        direction=direction,
        locations=locations,
        build_stock_url=build_stock_url,
    )


@bp.route("/stock/<int:item_id>")
def stock_detail(item_id: int):
    item = Item.query.get_or_404(item_id)
    all_locations = Location.query.order_by(Location.code).all()

    stock_rows = (
        db.session.query(
            Movement.location_id,
            func.coalesce(func.sum(Movement.quantity), 0).label("quantity"),
            func.max(Movement.date).label("updated_at"),
            func.max(pending_receipt_case()).label("pending_qty"),
        )
        .filter(Movement.item_id == item_id)
        .group_by(Movement.location_id)
        .all()
    )

    total_on_hand = db.session.query(
        func.coalesce(func.sum(Movement.quantity), 0)
    ).filter(Movement.item_id == item_id).scalar()
    total_on_hand = Decimal(total_on_hand or 0)

    latest_movements = (
        db.session.query(Movement.location_id, Movement.person, Movement.date)
        .filter(Movement.item_id == item_id)
        .order_by(Movement.location_id, Movement.date.desc(), Movement.id.desc())
        .all()
    )
    latest_by_location: dict[int, dict[str, object]] = {}
    for location_id, person, date in latest_movements:
        if location_id not in latest_by_location:
            latest_by_location[location_id] = {"person": person, "date": date}

    locations_map = {loc.id: loc for loc in all_locations}
    batch_balances = _get_item_location_batch_balances(item_id)
    locations = []
    for location_id, quantity, updated_at, pending_qty in stock_rows:
        quantity = Decimal(quantity or 0)
        is_pending = bool(pending_qty)
        if quantity == 0 and not is_pending:
            continue
        location = locations_map.get(location_id)
        latest = latest_by_location.get(location_id, {})
        batch_entries = batch_balances.get(location_id, [])
        if len(batch_entries) == 1:
            batch_entry = batch_entries[0]
            batch_label = batch_entry["lot_number"] or "Unbatched"
            batch_id = batch_entry["batch_id"]
        elif len(batch_entries) > 1:
            batch_label = "Multiple lots"
            batch_id = None
        else:
            batch_label = "Unbatched"
            batch_id = None
        locations.append(
            {
                "location": location,
                "quantity": quantity,
                "updated_at": updated_at or latest.get("date"),
                "updated_by": latest.get("person"),
                "batch_label": batch_label,
                "batch_id": batch_id,
                "batch_count": len(batch_entries),
                "pending_qty": is_pending,
            }
        )

    existing_location_ids = {row["location"].id for row in locations if row["location"]}
    available_locations = [
        loc for loc in all_locations if loc.id not in existing_location_ids
    ]
    transfer_from_locations = [
        row for row in locations if row["location"] and (row["quantity"] or 0) > 0
    ]
    can_edit = current_user.is_authenticated and (
        current_user.has_any_role(("admin",)) or is_superuser()
    )

    batch_count = (
        Batch.query.filter(Batch.item_id == item_id, Batch.removed_at.is_(None)).count()
    )

    return render_template(
        "inventory/stock_detail.html",
        item=item,
        total_on_hand=total_on_hand,
        batch_count=batch_count,
        locations=locations,
        available_locations=available_locations,
        all_locations=all_locations,
        transfer_from_locations=transfer_from_locations,
        can_edit=can_edit,
        remove_reasons=_get_remove_reasons(),
        next_url=url_for("inventory.stock_detail", item_id=item_id),
    )


@bp.post("/stock/<int:item_id>/set_quantity")
@require_admin_or_superuser
def set_stock_quantity(item_id: int):
    item = Item.query.get_or_404(item_id)
    location_id = request.form.get("location_id", type=int)
    desired_qty = _parse_stock_quantity(request.form.get("quantity"))
    reference = request.form.get("reference", "Stock Adjustment").strip() or "Stock Adjustment"

    if location_id is None:
        flash("Select a valid location.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    location = Location.query.get(location_id)
    if location is None:
        flash("Location not found.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    if desired_qty is None:
        flash("Enter a valid quantity.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))
    if desired_qty < 0:
        flash("Quantity cannot be negative.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    current_qty = _get_location_on_hand(item_id, location_id)
    delta = desired_qty - current_qty
    if delta == 0:
        flash("No quantity change detected.", "info")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    with db.session.begin_nested():
        movement = Movement(
            item_id=item.id,
            location_id=location_id,
            quantity=delta,
            movement_type="ADJUST",
            person=_movement_person(),
            reference=reference,
        )
        db.session.add(movement)
    db.session.commit()

    flash(
        f"Updated {item.sku} at {location.code} to {desired_qty}.",
        "success",
    )
    return redirect(url_for("inventory.stock_detail", item_id=item_id))


@bp.post("/stock/<int:item_id>/transfer")
@require_admin_or_superuser
def transfer_stock(item_id: int):
    item = Item.query.get_or_404(item_id)
    from_location_id = request.form.get("from_location_id", type=int)
    to_location_id = request.form.get("to_location_id", type=int)
    qty = _parse_stock_quantity(request.form.get("quantity"))
    reference = request.form.get("reference", "Stock Transfer").strip() or "Stock Transfer"

    if not from_location_id or not to_location_id:
        flash("Select both source and destination locations.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))
    if from_location_id == to_location_id:
        flash("Transfer locations must be different.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))
    if qty is None or qty <= 0:
        flash("Enter a transfer quantity greater than zero.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    from_location = Location.query.get(from_location_id)
    to_location = Location.query.get(to_location_id)
    if not from_location or not to_location:
        flash("Invalid transfer location selection.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    from_balance = _get_location_on_hand(item_id, from_location_id)
    if qty > from_balance:
        flash("Not enough stock in the selected location.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    with db.session.begin_nested():
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=from_location_id,
                quantity=-qty,
                movement_type="MOVE_OUT",
                person=_movement_person(),
                reference=reference,
            )
        )
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=to_location_id,
                quantity=qty,
                movement_type="MOVE_IN",
                person=_movement_person(),
                reference=reference,
            )
        )
        # Apply smart location assignment for the destination location.
        apply_smart_item_locations(item, to_location_id, db.session)
    db.session.commit()

    flash(
        f"Transferred {qty} of {item.sku} from {from_location.code} to {to_location.code}.",
        "success",
    )
    return redirect(url_for("inventory.stock_detail", item_id=item_id))


@bp.post("/stock/<int:item_id>/add_location")
@require_admin_or_superuser
def add_stock_location(item_id: int):
    item = Item.query.get_or_404(item_id)
    location_id = request.form.get("location_id", type=int)
    qty = _parse_stock_quantity(request.form.get("quantity")) or Decimal("0")
    reference = request.form.get("reference", "Add Location").strip() or "Add Location"

    if location_id is None:
        flash("Select a location to add.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    location = Location.query.get(location_id)
    if location is None:
        flash("Location not found.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    if qty < 0:
        flash("Quantity cannot be negative.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    existing = Movement.query.filter_by(
        item_id=item_id, location_id=location_id
    ).first()
    if existing:
        flash("This item already has activity at that location.", "warning")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    with db.session.begin_nested():
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location_id,
                quantity=qty,
                movement_type="ADJUST",
                person=_movement_person(),
                reference=reference,
            )
        )
        # Apply smart location assignment for the newly added location.
        apply_smart_item_locations(item, location_id, db.session)
    db.session.commit()

    flash(
        f"Added {location.code} for {item.sku} with {qty} on hand.",
        "success",
    )
    return redirect(url_for("inventory.stock_detail", item_id=item_id))


@bp.post("/stock/<int:item_id>/remove_location")
@require_admin_or_superuser
def remove_stock_location(item_id: int):
    item = Item.query.get_or_404(item_id)
    location_id = request.form.get("location_id", type=int)
    reference = request.form.get("reference", "Remove Location").strip() or "Remove Location"

    if location_id is None:
        flash("Select a location to remove.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    location = Location.query.get(location_id)
    if location is None:
        flash("Location not found.", "danger")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    current_qty = _get_location_on_hand(item_id, location_id)
    if current_qty == 0:
        flash("Location already has zero stock.", "info")
        return redirect(url_for("inventory.stock_detail", item_id=item_id))

    with db.session.begin_nested():
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location_id,
                quantity=-current_qty,
                movement_type="ADJUST",
                person=_movement_person(),
                reference=reference,
            )
        )
    db.session.commit()

    flash(
        f"Cleared stock for {item.sku} at {location.code}.",
        "success",
    )
    return redirect(url_for("inventory.stock_detail", item_id=item_id))


@bp.post("/remove_from_location")
@require_admin_or_superuser
def remove_from_location():
    item_id = request.form.get("item_id", type=int)
    location_id = request.form.get("location_id", type=int)
    raw_batch_id = (request.form.get("batch_id") or "").strip()
    remove_mode = (request.form.get("remove_mode") or "all").strip()
    reason = (request.form.get("reason") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    next_url = request.form.get("next") or url_for("inventory.list_stock")

    if item_id is None or location_id is None:
        flash("Select a valid item and location.", "danger")
        return redirect(next_url)

    item = Item.query.get(item_id)
    location = Location.query.get(location_id)
    if item is None or location is None:
        flash("Item or location not found.", "danger")
        return redirect(next_url)

    remove_reasons = _get_remove_reasons()
    if not reason or (remove_reasons and reason not in remove_reasons):
        flash("Select a valid removal reason.", "danger")
        return redirect(next_url)

    batch_id = None
    if raw_batch_id and raw_batch_id.lower() != "none":
        try:
            batch_id = int(raw_batch_id)
        except (TypeError, ValueError):
            flash("Invalid lot/batch selection.", "danger")
            return redirect(next_url)

    removal_entries: list[tuple[int | None, Decimal]] = []
    if batch_id is not None:
        batch = Batch.query.get(batch_id)
        if batch is None or batch.item_id != item_id:
            flash("Invalid lot/batch selection.", "danger")
            return redirect(next_url)
        available = _get_location_on_hand_by_batch(item_id, location_id, batch_id)
        if available <= 0:
            flash("No stock available to remove for that lot/batch.", "info")
            return redirect(next_url)
        if remove_mode == "partial":
            qty = _parse_stock_quantity(request.form.get("quantity"))
            if qty is None or qty <= 0:
                flash("Enter a valid removal quantity.", "danger")
                return redirect(next_url)
            if qty > available:
                flash("Removal quantity exceeds available stock.", "danger")
                return redirect(next_url)
        else:
            qty = available
        removal_entries.append((batch_id, qty))
    else:
        batch_rows = (
            db.session.query(
                Movement.batch_id,
                Batch.lot_number,
                func.coalesce(func.sum(Movement.quantity), 0).label("on_hand"),
            )
            .outerjoin(Batch, Batch.id == Movement.batch_id)
            .filter(Movement.item_id == item_id, Movement.location_id == location_id)
            .filter(or_(Movement.batch_id.is_(None), Batch.removed_at.is_(None)))
            .group_by(Movement.batch_id, Batch.lot_number)
            .all()
        )
        batch_entries = [
            (batch_id, Decimal(on_hand or 0))
            for batch_id, _, on_hand in batch_rows
            if Decimal(on_hand or 0) > 0
        ]
        if not batch_entries:
            flash("No stock available to remove at that location.", "info")
            return redirect(next_url)
        if remove_mode == "partial":
            if len(batch_entries) != 1:
                flash(
                    "Partial removal requires a specific lot/batch selection.",
                    "danger",
                )
                return redirect(next_url)
            available = batch_entries[0][1]
            qty = _parse_stock_quantity(request.form.get("quantity"))
            if qty is None or qty <= 0:
                flash("Enter a valid removal quantity.", "danger")
                return redirect(next_url)
            if qty > available:
                flash("Removal quantity exceeds available stock.", "danger")
                return redirect(next_url)
            removal_entries.append((batch_entries[0][0], qty))
        else:
            removal_entries = batch_entries

    reference = reason if not notes else f"{reason} - {notes}"
    total_removed = Decimal("0")
    with db.session.begin_nested():
        for entry_batch_id, qty in removal_entries:
            if qty <= 0:
                continue
            db.session.add(
                Movement(
                    item_id=item_id,
                    batch_id=entry_batch_id,
                    location_id=location_id,
                    quantity=-qty,
                    movement_type="REMOVE_FROM_LOCATION",
                    person=_movement_person(),
                    reference=reference,
                )
            )
            total_removed += qty
    db.session.commit()

    flash(
        f"Removed {total_removed} of {item.sku} from {location.code}.",
        "success",
    )
    return redirect(next_url)


@bp.post("/pending/<int:receipt_id>/set_qty")
@login_required
def set_pending_receipt_qty(receipt_id: int):
    receipt = Movement.query.get_or_404(receipt_id)
    next_url = request.form.get("next") or url_for("inventory.list_locations")

    if not _is_pending_receipt(receipt):
        flash("Selected receipt is no longer pending.", "warning")
        return redirect(next_url)

    qty = _parse_stock_quantity(request.form.get("quantity"))
    if qty is None or qty <= 0:
        flash("Enter a valid quantity to set.", "danger")
        return redirect(next_url)

    batch = Batch.query.get(receipt.batch_id) if receipt.batch_id else None

    with db.session.begin_nested():
        receipt.reference = _resolve_pending_reference(receipt.reference, "resolved")
        if batch:
            batch.quantity = (batch.quantity or 0) + qty
        db.session.add(
            Movement(
                item_id=receipt.item_id,
                batch_id=receipt.batch_id,
                location_id=receipt.location_id,
                quantity=qty,
                movement_type="RECEIPT",
                person=_movement_person(),
                reference="Pending qty set",
            )
        )
    db.session.commit()

    flash("Pending quantity updated.", "success")
    return redirect(next_url)


@bp.post("/pending/<int:receipt_id>/move")
@login_required
def move_pending_receipt(receipt_id: int):
    receipt = Movement.query.get_or_404(receipt_id)
    next_url = request.form.get("next") or url_for("inventory.list_locations")
    to_location_id = request.form.get("to_location_id", type=int)

    if not _is_pending_receipt(receipt):
        flash("Selected receipt is no longer pending.", "warning")
        return redirect(next_url)

    if not to_location_id:
        flash("Select a destination location.", "danger")
        return redirect(next_url)

    to_location = Location.query.get(to_location_id)
    if not to_location:
        flash("Destination location not found.", "danger")
        return redirect(next_url)

    if receipt.location_id == to_location_id:
        flash("Destination location must be different.", "danger")
        return redirect(next_url)

    from_location = Location.query.get(receipt.location_id)
    from_code = from_location.code if from_location else "Unknown"

    with db.session.begin_nested():
        receipt.location_id = to_location_id
        db.session.add(
            Movement(
                item_id=receipt.item_id,
                batch_id=receipt.batch_id,
                location_id=to_location_id,
                quantity=Decimal("0"),
                movement_type="MOVE_PENDING",
                person=_movement_person(),
                reference=f"Pending qty move {from_code} -> {to_location.code}",
            )
        )
    db.session.commit()

    flash("Pending receipt moved to new location.", "success")
    return redirect(next_url)


@bp.post("/pending/<int:receipt_id>/remove")
@require_admin_or_superuser
def remove_pending_receipt(receipt_id: int):
    receipt = Movement.query.get_or_404(receipt_id)
    next_url = request.form.get("next") or url_for("inventory.list_locations")
    reason = (request.form.get("reason") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    if not _is_pending_receipt(receipt):
        flash("Selected receipt is no longer pending.", "warning")
        return redirect(next_url)

    remove_reasons = _get_remove_reasons()
    if not reason or (remove_reasons and reason not in remove_reasons):
        flash("Select a valid removal reason.", "danger")
        return redirect(next_url)

    reference = reason if not notes else f"{reason} - {notes}"

    with db.session.begin_nested():
        receipt.reference = _resolve_pending_reference(receipt.reference, "voided")
        db.session.add(
            Movement(
                item_id=receipt.item_id,
                batch_id=receipt.batch_id,
                location_id=receipt.location_id,
                quantity=Decimal("0"),
                movement_type="REMOVE_FROM_LOCATION",
                person=_movement_person(),
                reference=reference,
            )
        )
    db.session.commit()

    flash("Pending receipt cleared from location.", "success")
    return redirect(next_url)


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

            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames:
                flash("Uploaded CSV does not contain a header row.", "danger")
                _remove_import_csv("stock", import_token)
                return redirect(url_for("inventory.import_stock"))

            auto_mappings = resolve_import_mappings(
                reader.fieldnames, STOCK_IMPORT_FIELDS, STOCK_HEADER_ALIASES
            )
            selected_mappings = {}
            for field_cfg in STOCK_IMPORT_FIELDS:
                selected_header = request.form.get(f"mapping_{field_cfg['field']}", "")
                if selected_header:
                    selected_mappings[field_cfg["field"]] = selected_header
                elif field_cfg["field"] in auto_mappings:
                    selected_mappings[field_cfg["field"]] = auto_mappings[field_cfg["field"]]

            missing_required = [
                field_cfg["label"]
                for field_cfg in STOCK_IMPORT_FIELDS
                if field_cfg["required"] and field_cfg["field"] not in selected_mappings
            ]
            if missing_required:
                expected = ", ".join(expected_headers(STOCK_CSV_COLUMNS))
                flash(
                    "Missing required columns: "
                    + ", ".join(missing_required)
                    + f". Expected headers include: {expected}.",
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

            if (
                "item_id" not in selected_mappings
                and "sku" not in selected_mappings
            ):
                expected = ", ".join(expected_headers(STOCK_CSV_COLUMNS))
                flash(
                    "Stock imports require item_id or sku. "
                    f"Expected headers include: {expected}.",
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

            _log_unmapped_headers("stock", reader.fieldnames, selected_mappings)

            items = Item.query.all()
            item_map_by_sku = {i.sku: i for i in items}
            item_map_by_id = {i.id: i for i in items}
            loc_map_by_code = {l.code: l for l in Location.query.all()}
            loc_map_by_id = {l.id: l for l in loc_map_by_code.values()}

            placeholder_location = _ensure_placeholder_location(loc_map_by_code)

            def extract(row, field):
                header = selected_mappings.get(field)
                if not header:
                    return ""
                value = row.get(header)
                return value if value is not None else ""

            count_new, count_updated = 0, 0
            for row in reader:
                item_id = _parse_int(extract(row, "item_id"))
                sku = extract(row, "sku").strip()
                if not item_id and not sku:
                    continue

                quantity_raw = extract(row, "quantity").strip()
                qty = _parse_decimal(quantity_raw)
                if qty is None:
                    continue

                loc_id = _parse_int(extract(row, "location_id"))
                loc_code = extract(row, "location_code").strip()
                lot_number = extract(row, "lot_number").strip() or None
                person = extract(row, "person").strip() or None
                reference = extract(row, "reference").strip() or "Bulk Adjust"

                item = item_map_by_id.get(item_id) if item_id else None
                if item is None and sku:
                    item = item_map_by_sku.get(sku)
                if not item:
                    continue

                location = loc_map_by_id.get(loc_id) if loc_id is not None else None
                if location is None and loc_code:
                    location = loc_map_by_code.get(loc_code)
                if not location:
                    location = placeholder_location

                batch_id = _parse_int(extract(row, "batch_id"))
                received_date_raw = extract(row, "received_date").strip()
                expiration_date_raw = extract(row, "expiration_date").strip()
                supplier_name = extract(row, "supplier_name").strip() or None
                supplier_code = extract(row, "supplier_code").strip() or None
                purchase_order = extract(row, "purchase_order").strip() or None
                notes = extract(row, "notes").strip() or None

                batch = None
                if batch_id is not None:
                    batch = Batch.query.get(batch_id)
                if batch is None and lot_number:
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
                if batch:
                    received_date = _parse_iso_datetime(received_date_raw)
                    expiration_date = _parse_iso_date(expiration_date_raw)
                    if received_date is not None:
                        batch.received_date = received_date
                    if expiration_date is not None:
                        batch.expiration_date = expiration_date
                    if "supplier_name" in selected_mappings:
                        batch.supplier_name = supplier_name
                    if "supplier_code" in selected_mappings:
                        batch.supplier_code = supplier_code
                    if "purchase_order" in selected_mappings:
                        batch.purchase_order = purchase_order
                    if "notes" in selected_mappings:
                        batch.notes = notes

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

        headers = next(csv.reader(io.StringIO(csv_text)), [])
        auto_mappings = resolve_import_mappings(
            headers, STOCK_IMPORT_FIELDS, STOCK_HEADER_ALIASES
        )
        context = _prepare_stock_import_mapping_context(
            csv_text, selected_mappings=auto_mappings
        )
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
    Export current stock balances to CSV.
    """
    rows = (
        db.session.query(
            Movement.item_id,
            Movement.batch_id,
            Movement.location_id,
            func.coalesce(func.sum(Movement.quantity), 0).label("quantity"),
        )
        .outerjoin(Batch, Batch.id == Movement.batch_id)
        .filter(or_(Movement.batch_id.is_(None), Batch.removed_at.is_(None)))
        .group_by(Movement.item_id, Movement.batch_id, Movement.location_id)
        .all()
    )

    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    def iter_rows():
        for item_id, batch_id, location_id, quantity in rows:
            item = items.get(item_id)
            location = locations.get(location_id)
            batch = batches.get(batch_id) if batch_id else None
            yield {
                "item_id": item_id,
                "sku": item.sku if item else None,
                "name": item.name if item else None,
                "primary_location_code": (
                    item.default_location.code if item and item.default_location else None
                ),
                "location_id": location_id,
                "location_code": location.code if location else None,
                "batch_id": batch_id,
                "lot_number": batch.lot_number if batch else None,
                "quantity": quantity,
                "person": None,
                "reference": None,
                "received_date": batch.received_date if batch else None,
                "expiration_date": batch.expiration_date if batch else None,
                "supplier_name": batch.supplier_name if batch else None,
                "supplier_code": batch.supplier_code if batch else None,
                "purchase_order": batch.purchase_order if batch else None,
                "notes": batch.notes if batch else None,
            }

    filename = f"stock_export_{date.today().isoformat()}.csv"
    return export_rows_to_csv(iter_rows(), STOCK_CSV_COLUMNS, filename)



############################
# RECEIVING ROUTES
############################
@bp.route("/receiving", methods=["GET", "POST"])
def receiving():
    placeholder_location = Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).one_or_none()
    if not placeholder_location:
        placeholder_location = _ensure_placeholder_location({})
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
    if not form_defaults["location_id"] and placeholder_location:
        form_defaults["location_id"] = str(placeholder_location.id)

    selected_location = None
    if form_defaults["location_id"]:
        try:
            selected_location = db.session.get(Location, int(form_defaults["location_id"]))
        except (TypeError, ValueError):
            selected_location = Location.query.filter(
                func.lower(Location.code) == form_defaults["location_id"].lower()
            ).first()
    if selected_location is None:
        selected_location = placeholder_location

    if request.method == "POST":
        sku = request.form["sku"].strip()
        defer_qty = (
            current_user.is_authenticated
            and request.form.get("defer_qty") == "1"
        )
        qty_raw = request.form.get("qty", "").strip()
        person = request.form["person"].strip()
        po_number = request.form.get("po_number", "").strip() or None
        location = _resolve_location_from_form(request.form.get("location_id"))
        if location is None:
            flash("Select a valid location before submitting the receipt.", "danger")
            return redirect(url_for("inventory.receiving"))
        location_id = location.id

        qty_missing = not qty_raw

        if defer_qty or qty_missing:
            qty = 0
        else:
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                flash("Enter a valid quantity or leave it blank to defer counting.", "danger")
                return redirect(url_for("inventory.receiving"))

        item = Item.query.filter_by(sku=sku).first()
        if not item:
            flash(f"Item with SKU {sku} not found.", "danger")
            return redirect(url_for("inventory.receiving"))

        # ?? Auto-generate lot number: SKU-YYMMDD-##
        today_str = datetime.now().strftime("%y%m%d")
        base_lot = f"{item.sku}-{today_str}"

        existing_lots = (
            db.session.query(func.count(Batch.id))
            .filter(
                Batch.item_id == item.id,
                Batch.lot_number.like(f"{base_lot}-%"),
            )
            .scalar()
            or 0
        )

        seq_num = existing_lots + 1
        lot_number = f"{base_lot}-{seq_num:02d}"

        # Create or update batch
        batch_columns = {col["name"] for col in inspect(db.engine).get_columns("batch")}
        if "removed_at" in batch_columns:
            batch = Batch(item_id=item.id, lot_number=lot_number, quantity=0)
            db.session.add(batch)
            db.session.flush()
            batch_id = batch.id
        else:
            batch_payload = {
                "item_id": item.id,
                "lot_number": lot_number,
                "quantity": 0,
                "removed_at": None,
                "received_date": datetime.utcnow(),
                "expiration_date": None,
                "supplier_name": None,
                "supplier_code": None,
                "purchase_order": po_number,
                "notes": None,
            }
            filtered_payload = {
                key: value for key, value in batch_payload.items() if key in batch_columns
            }
            result = db.session.execute(
                Batch.__table__.insert().values(**filtered_payload).returning(Batch.id)
            )
            batch_id = result.scalar_one()
        batch = Batch.active().filter_by(id=batch_id).first()
        if not batch:
            flash("The selected batch is no longer available.", "warning")
            db.session.rollback()
            return redirect(url_for("inventory.receiving"))
        batch.quantity = (batch.quantity or 0) + qty
        if po_number:
            batch.purchase_order = po_number

        # Record movement
        reference = "PO Receipt" if po_number else "Receipt"
        if defer_qty or qty_missing:
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
        # Apply smart location assignment after the receipt is recorded.
        apply_smart_item_locations(item, location_id, db.session)
        db.session.commit()

        if defer_qty or qty_missing:
            flash(f"Receiving recorded! Lot: {lot_number}", "success")
            flash(
                "Receiving recorded without a quantity. Update the batch once the count is known.",
                "info",
            )
            return redirect(url_for("inventory.receiving"))

        try:
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

    can_defer_without_qty = current_user.is_authenticated

    return render_template(
        "inventory/receiving.html",
        records=records,
        form_defaults=form_defaults,
        default_location=default_location,
        item_details=item_details,
        can_defer_without_qty=can_defer_without_qty,
        selected_location=selected_location,
        selected_location_label=(
            _format_location_label(selected_location)
            if selected_location
            else UNASSIGNED_LOCATION_CODE
        ),
        unassigned_location=placeholder_location,
        unassigned_location_label=(
            _format_location_label(placeholder_location)
            if placeholder_location
            else UNASSIGNED_LOCATION_CODE
        ),
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
@bp.get("/move/location/<int:location_id>/lines")
def move_location_lines(location_id: int):
    location = Location.query.get(location_id)
    if location is None:
        abort(404)

    lines = get_location_inventory_lines(location_id)
    return jsonify({"location": {"id": location.id, "code": location.code}, "lines": lines})


@bp.route("/move", methods=["GET", "POST"])
def move_home():
    locations = Location.query.order_by(Location.code).all()
    location_ids = {location.id for location in locations}
    default_from_location_id = session.get("last_move_from_location_id")
    if default_from_location_id not in location_ids:
        default_from_location_id = locations[0].id if locations else None

    if request.method == "POST":
        from_loc_id = request.form.get("from_location_id", type=int)
        to_loc_id = request.form.get("to_location_id", type=int)
        reference = request.form.get("reference", "Stock Transfer").strip() or "Stock Transfer"
        raw_lines = request.form.get("lines", "").strip()

        try:
            payload = json.loads(raw_lines) if raw_lines else []
        except json.JSONDecodeError:
            payload = []

        lines: list[MoveLineRequest] = []
        for entry in payload if isinstance(payload, list) else []:
            try:
                item_id = int(entry.get("item_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            raw_batch_id = entry.get("batch_id")
            batch_id = None
            if raw_batch_id not in (None, "", "none"):
                try:
                    batch_id = int(raw_batch_id)
                except (TypeError, ValueError):
                    batch_id = None
            qty = _parse_stock_quantity(str(entry.get("move_qty", "")))
            if qty is None:
                continue
            lines.append(
                MoveLineRequest(item_id=item_id, batch_id=batch_id, quantity=qty)
            )

        try:
            result = move_inventory_lines(
                lines=lines,
                from_location_id=from_loc_id,
                to_location_id=to_loc_id,
                person=_movement_person(),
                reference=reference,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("inventory.move_home"))
        except IntegrityError:
            db.session.rollback()
            flash("Unable to complete the move. Please try again.", "danger")
            return redirect(url_for("inventory.move_home"))

        db.session.commit()
        session["last_move_from_location_id"] = from_loc_id
        flash(
            f"Moved {result['total_qty']} total units across {result['total_lines']} lines.",
            "success",
        )
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

    return render_template(
        "inventory/move.html",
        locations=locations,
        default_from_location_id=default_from_location_id,
        records=records,
        items_map=items_map,
        locations_map=locations_map,
        batches_map=batches_map,
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
