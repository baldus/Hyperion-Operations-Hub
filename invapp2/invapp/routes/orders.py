import base64
import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload

from invapp.extensions import db, login_manager
from invapp.auth import blueprint_page_guard
from invapp.security import require_roles
from invapp.models import (
    BillOfMaterial,
    BillOfMaterialComponent,
    Batch,
    GateOrderDetail,
    ImportBatch,
    Item,
    Location,
    Movement,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    Reservation,
    RoutingStep,
    RoutingStepComponent,
    RoutingStepConsumption,
)
from invapp.login import current_user
from invapp.superuser import is_superuser
from invapp.gate_parser import GatePartNumberError, parse_gate_part_number

bp = Blueprint("orders", __name__, url_prefix="/orders")

bp.before_request(blueprint_page_guard("orders"))

ORDER_TYPE_CHOICES = ("Gates", "COP's", "Operators", "Controllers")
GATE_ROUTING_STEPS = ("Framing", "Assembly", "Inspection", "Packaging")
IMPORT_REQUIRED_COLUMNS = {
    "so/proposal no.": "order_number",
    "ship by": "ship_by",
    "customer": "customer",
    "item id": "item_number",
    "item description": "item_description",
    "qty on order": "quantity",
    "item type": "order_type",
}
IMPORT_DISPLAY_COLUMNS = (
    ("SO/Proposal No.", "order_number"),
    ("Ship By", "ship_by"),
    ("Customer", "customer"),
    ("Item ID", "item_number"),
    ("Item Description", "item_description"),
    ("Qty on Order", "quantity"),
    ("Item Type", "order_type"),
)


def _ensure_order_management_access():
    if is_superuser():
        return None

    if current_user.is_authenticated and current_user.has_role("admin"):
        return None

    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    abort(403)


def _search_filter(query, search_term):
    if not search_term:
        return query

    like_term = f"%{search_term}%"
    return query.outerjoin(Order.order_lines).outerjoin(OrderLine.item).filter(
        or_(
            Order.order_number.ilike(like_term),
            Item.sku.ilike(like_term),
            Item.name.ilike(like_term),
        )
    )


def _overdue_ordering():
    """Return ordering expressions to place overdue orders first."""

    promise_overdue = case(
        (Order.promised_date < func.current_date(), 1),
        else_=0,
    )
    ship_overdue = case(
        (Order.scheduled_ship_date < func.current_date(), 1),
        else_=0,
    )

    return promise_overdue.desc(), ship_overdue.desc()


def _rebalance_priorities():
    """Normalize priority values for active, reorderable orders."""

    reorderable_orders = (
        Order.query.filter(Order.status.in_(OrderStatus.RESERVABLE_STATES))
        .order_by(
            Order.priority,
            Order.promised_date.is_(None),
            Order.promised_date,
            Order.order_number,
        )
        .all()
    )

    for new_priority, order in enumerate(reorderable_orders, start=1):
        if order.priority != new_priority:
            order.priority = new_priority


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _parse_ship_date(raw_value):
    if raw_value is None or raw_value == "":
        return None, None

    if isinstance(raw_value, (int, float)):
        try:
            excel_start = datetime(1899, 12, 30)
            converted = excel_start + timedelta(days=int(raw_value))
            return converted.date(), None
        except Exception:
            return None, "Unable to convert Excel serial date"

    value = _normalize_text(str(raw_value))
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date(), None
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).date(), None
    except Exception:
        return None, "Unable to parse Ship By date"


def _parse_quantity_value(raw_value):
    if raw_value is None or str(raw_value).strip() == "":
        return None
    try:
        cleaned = str(raw_value).replace(",", "")
        qty = Decimal(cleaned)
        if qty <= 0:
            return None
        return int(qty.to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _append_description_note(order: Order, description: str):
    normalized = _normalize_text(description)
    if not normalized:
        return
    line = f"Imported Description: {normalized}"
    notes = order.general_notes or ""
    if line.strip() and line not in notes:
        if notes and not notes.endswith("\n"):
            notes += "\n"
        notes += line
        order.general_notes = notes


def _normalize_headers(headers):
    return {(header or "").strip().lower(): header for header in headers or ()}


def _extract_import_rows_from_csv(upload, errors):
    upload.stream.seek(0)
    try:
        wrapper = io.TextIOWrapper(upload.stream, encoding="utf-8-sig")
    except Exception:
        errors.append("Unable to read CSV file contents.")
        return []

    reader = csv.DictReader(wrapper)
    if not reader.fieldnames:
        errors.append("CSV file is empty.")
        return []

    normalized_headers = _normalize_headers(reader.fieldnames)
    rows = []
    for row_number, row in enumerate(reader, start=2):
        rows.append({"row_number": row_number, "data": row, "headers": normalized_headers})
    return rows


def _extract_import_rows_from_xlsx(upload, errors):
    def column_index(cell_ref: str) -> int:
        letters = "".join(ch for ch in cell_ref if ch.isalpha())
        result = 0
        for char in letters:
            result = result * 26 + (ord(char.upper()) - ord("A") + 1)
        return result - 1

    upload.stream.seek(0)
    try:
        with zipfile.ZipFile(upload.stream) as archive:
            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in archive.namelist():
                errors.append("Could not locate the first worksheet in the Excel file.")
                return []

            shared_strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in shared_root.findall("a:si", ns):
                    texts = [node.text or "" for node in si.findall(".//a:t", ns)]
                    shared_strings.append("".join(texts))

            sheet_root = ElementTree.fromstring(archive.read(sheet_name))
    except Exception:
        errors.append("Unable to read Excel file. Ensure it is a valid .xlsx workbook.")
        return []

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    headers: list[str] = []
    rows = []

    for row_idx, row in enumerate(sheet_root.findall("a:sheetData/a:row", ns), start=1):
        values: dict[int, str] = {}
        for cell in row.findall("a:c", ns):
            ref = cell.get("r", "")
            idx = column_index(ref)
            value_element = cell.find("a:v", ns)
            if value_element is None:
                continue
            raw_value = value_element.text
            cell_type = cell.get("t")
            if cell_type == "s":
                try:
                    raw_value = shared_strings[int(raw_value)]
                except Exception:
                    raw_value = raw_value or ""
            values[idx] = raw_value

        if not values:
            continue

        if row_idx == 1:
            headers = [values.get(i, "") for i in range(max(values.keys()) + 1)]
            continue

        row_data = {headers[i]: values.get(i, "") for i in range(len(headers))}
        if not any(row_data.values()):
            continue
        rows.append({"row_number": row_idx, "data": row_data, "headers": _normalize_headers(headers)})

    if not headers:
        errors.append("Excel file is missing a header row.")
    return rows


def _extract_order_import_rows(upload):
    filename = (upload.filename or "").lower()
    errors: list[str] = []
    if filename.endswith(".csv"):
        rows = _extract_import_rows_from_csv(upload, errors)
    elif filename.endswith(".xlsx"):
        rows = _extract_import_rows_from_xlsx(upload, errors)
    else:
        errors.append("Only .csv and .xlsx files are supported.")
        rows = []

    return rows, errors


def _map_row_data(row_entry):
    normalized_headers = row_entry["headers"]
    raw_row = row_entry["data"]
    mapped = {value: "" for value in IMPORT_REQUIRED_COLUMNS.values()}
    for required_name, mapped_key in IMPORT_REQUIRED_COLUMNS.items():
        source_header = normalized_headers.get(required_name)
        if source_header is None:
            continue
        mapped[mapped_key] = raw_row.get(source_header, "")
    return mapped


def _validate_required_columns(normalized_headers):
    missing = [
        display
        for key, value in IMPORT_REQUIRED_COLUMNS.items()
        if key not in normalized_headers
        for display, mapped in IMPORT_DISPLAY_COLUMNS
        if mapped == value
    ]
    return sorted(set(missing))


def _generate_error_report(errors):
    if not errors:
        return None
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["row", "message"])
    for entry in errors:
        writer.writerow([entry["row"], entry["message"]])
    csv_bytes = output.getvalue().encode("utf-8")
    b64 = base64.b64encode(csv_bytes).decode("ascii")
    return f"data:text/csv;base64,{b64}"


def _load_mapped_import_rows(upload):
    raw_rows, file_errors = _extract_order_import_rows(upload)
    if file_errors:
        return [], file_errors, []
    if not raw_rows:
        return [], [], []

    normalized_headers = raw_rows[0]["headers"]
    missing = _validate_required_columns(normalized_headers)
    if missing:
        return [], [], missing

    mapped_rows = []
    for entry in raw_rows:
        mapped = _map_row_data(entry)
        if all(not (value or "").strip() for value in mapped.values()):
            continue
        mapped_rows.append({"row_number": entry["row_number"], **mapped})
    return mapped_rows, [], []


def _perform_order_import(mapped_rows, filename):
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    created = 0
    updated = 0
    skipped = 0
    imported_numbers: set[str] = set()
    flagged_for_review = 0

    with db.session.begin_nested():
        batch = ImportBatch(
            filename=filename,
            uploaded_by=getattr(current_user, "username", None),
            row_count=len(mapped_rows),
        )
        db.session.add(batch)
        db.session.flush()

        for entry in mapped_rows:
            row_number = entry["row_number"]
            order_number = _normalize_text(entry.get("order_number"))
            if not order_number:
                errors.append({"row": row_number, "message": "Missing SO/Proposal No."})
                skipped += 1
                continue

            quantity = _parse_quantity_value(entry.get("quantity"))
            if quantity is None:
                errors.append(
                    {"row": row_number, "message": "Qty on Order must be a positive number."}
                )
                skipped += 1
                continue

            ship_date, ship_warning = _parse_ship_date(entry.get("ship_by"))
            if ship_warning:
                warnings.append({"row": row_number, "message": ship_warning})

            customer = _normalize_text(entry.get("customer")) or None
            item_number = _normalize_text(entry.get("item_number")) or "Unknown"
            order_type = _normalize_text(entry.get("order_type")) or ORDER_TYPE_CHOICES[0]
            description = _normalize_text(entry.get("item_description"))

            order = Order.query.filter_by(order_number=order_number).first()
            if order is None:
                order = Order(order_number=order_number, status=OrderStatus.SCHEDULED)
                db.session.add(order)
                created += 1
            else:
                updated += 1

            order.customer_name = customer
            order.order_type = order_type
            order.scheduled_ship_date = ship_date
            order.last_import_batch_id = batch.id
            order.needs_review = False
            order.review_reason = None
            order.review_batch_id = None

            details = order.gate_details
            if details is None:
                details = GateOrderDetail(order=order, item_number=item_number, production_quantity=quantity)
                db.session.add(details)
            else:
                details.item_number = item_number
                details.production_quantity = quantity

            _append_description_note(order, description)
            imported_numbers.add(order.order_number)

        active_orders = Order.query.filter(Order.status.in_(OrderStatus.ACTIVE_STATES)).all()
        for order in active_orders:
            if order.order_number not in imported_numbers:
                order.needs_review = True
                order.review_reason = "Missing from latest import"
                order.review_batch_id = batch.id
                flagged_for_review += 1

        batch.created_count = created
        batch.updated_count = updated
        batch.skipped_count = skipped
        batch.error_count = len(errors)

    return {
        "batch": batch,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "warnings": warnings,
        "flagged": flagged_for_review,
    }


def _available_quantity(item_id: int) -> Decimal:
    """Return on-hand inventory minus active reservations for an item."""

    total_on_hand = Decimal(
        (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item_id)
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
                Reservation.item_id == item_id,
                Order.status.in_(OrderStatus.RESERVABLE_STATES),
            )
            .scalar()
        )
        or 0
    )

    available = total_on_hand - reserved_total
    return available if available > 0 else Decimal("0")


def _component_requirement(usage: RoutingStepComponent) -> Decimal:
    bom_component = usage.bom_component
    order_line = bom_component.order_line
    return Decimal(bom_component.quantity) * Decimal(order_line.quantity)


def _position_balance(item_id: int, batch_id: int | None, location_id: int) -> int:
    filters = [Movement.item_id == item_id, Movement.location_id == location_id]
    if batch_id is None:
        filters.append(Movement.batch_id.is_(None))
    else:
        filters.append(Movement.batch_id == batch_id)

    balance = (
        db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(*filters)
        .scalar()
    ) or 0
    return int(balance)


def _inventory_options(item_id: int):
    rows = (
        db.session.query(
            Movement.batch_id,
            Movement.location_id,
            func.coalesce(func.sum(Movement.quantity), 0).label("on_hand"),
        )
        .filter(Movement.item_id == item_id)
        .group_by(Movement.batch_id, Movement.location_id)
        .having(func.coalesce(func.sum(Movement.quantity), 0) > 0)
        .all()
    )

    batch_ids = {row.batch_id for row in rows if row.batch_id is not None}
    location_ids = {row.location_id for row in rows if row.location_id is not None}

    batches = {
        batch.id: batch
        for batch in Batch.query.filter(Batch.id.in_(batch_ids)).all()
    } if batch_ids else {}
    locations = {
        location.id: location
        for location in Location.query.filter(Location.id.in_(location_ids)).all()
    } if location_ids else {}

    options = []
    for batch_id, location_id, on_hand in rows:
        batch_label = "Unbatched"
        if batch_id is not None:
            batch = batches.get(batch_id)
            batch_label = batch.lot_number if batch else f"Batch {batch_id}"
        location_label = "Unknown"
        if location_id is not None:
            location = locations.get(location_id)
            location_label = location.code if location else f"Loc {location_id}"

        options.append(
            {
                "value": f"{batch_id if batch_id is not None else 'none'}::{location_id}",
                "batch_id": batch_id,
                "location_id": location_id,
                "label": f"{batch_label} @ {location_label} (avail {_format_quantity(on_hand)})",
                "available": float(on_hand),
            }
        )

    return sorted(options, key=lambda entry: entry["label"])


@bp.route("/api/parse_gate_part_number", methods=["POST"])
def parse_gate_part_number_api():
    guard_response = _ensure_order_management_access()
    if guard_response is not None:
        return guard_response

    payload = request.get_json(silent=True) or {}
    part_number = (payload.get("part_number") or "").strip()

    try:
        parsed = parse_gate_part_number(part_number)
    except GatePartNumberError as exc:
        return jsonify({"error": str(exc)}), 400

    response = {
        "material": parsed.material,
        "panel_material_color": parsed.panel_material_color,
        "handing": parsed.handing,
        "panel_count": parsed.panel_count,
        "legacy_panel_code": parsed.legacy_panel_code,
        "door_height_inches": parsed.door_height_inches,
        "door_height_display": parsed.door_height_display,
        "total_gate_height": parsed.door_height_inches,
        "parsed_format": parsed.parsed_format,
        "warnings": parsed.warnings,
    }

    autofill_fields = []
    if parsed.parsed_format.startswith("LEGACY"):
        response.update(
            {
                "panel_count": parsed.panel_count,
                "vision_panel_qty": parsed.vision_panel_qty,
                "vision_panel_color": parsed.vision_panel_color,
                "hardware_option": parsed.hardware_option,
                "adders": parsed.adders,
                "al_color": parsed.material,
                "insert_color": parsed.panel_material_color,
                "lead_post_direction": parsed.handing,
                "visi_panels": str(parsed.vision_panel_qty),
                "half_panel_color": parsed.vision_panel_color,
            }
        )
        autofill_fields = [
            field
            for field in [
                "panel_count" if parsed.panel_count is not None else None,
                "total_gate_height",
                "al_color",
                "insert_color",
                "lead_post_direction",
                "visi_panels",
                "half_panel_color",
                "hardware_option" if parsed.hardware_option is not None else None,
            ]
            if field is not None
        ]
    else:
        response.update(
            {
                "panel_count": parsed.panel_count,
                "vision_panel_qty": parsed.vision_panel_qty,
                "vision_panel_color": parsed.vision_panel_color,
                "hardware_option": parsed.hardware_option,
                "adders": parsed.adders,
                "al_color": parsed.material,
                "insert_color": parsed.panel_material_color,
                "lead_post_direction": parsed.handing,
                "visi_panels": str(parsed.vision_panel_qty),
                "half_panel_color": parsed.vision_panel_color,
            }
        )
        autofill_fields = list(
            {
                "panel_count",
                "total_gate_height",
                "al_color",
                "insert_color",
                "lead_post_direction",
                "visi_panels",
                "half_panel_color",
                "hardware_option",
                "adders",
            }
        )

    response["autofill_fields"] = autofill_fields
    return jsonify(response)


def _format_schedule_breakdown(buckets):
    if not buckets:
        return {"dates": [], "series": []}

    date_keys = sorted(
        buckets.keys(), key=lambda value: (value is None, value.toordinal() if value else 0)
    )
    date_labels = [value.isoformat() if value else "Unscheduled" for value in date_keys]

    categories = sorted(
        {category for totals in buckets.values() for category in totals.keys()}
    )

    series = []
    for category in categories:
        series.append(
            {
                "label": category,
                "data": [int(buckets[date_key].get(category, 0)) for date_key in date_keys],
            }
        )

    return {"dates": date_labels, "series": series}


def _parse_positive_quantity(raw_value, *, allow_zero=False):
    if raw_value is None:
        raise ValueError("Quantity is required")

    if isinstance(raw_value, (int, float, Decimal)):
        value = Decimal(str(raw_value))
    else:
        value = Decimal(str(raw_value).strip())

    if value == 0:
        if allow_zero:
            return value
        raise ValueError("Quantity must be greater than zero")
    if value < 0:
        raise ValueError("Quantity must be greater than zero")
    return value


def _parse_positive_int(raw_value, field_label, errors):
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        errors.append(f"{field_label} must be a whole number.")
        return None

    if value <= 0:
        errors.append(f"{field_label} must be greater than zero.")
        return None
    return value


def _parse_decimal(raw_value, field_label, errors):
    try:
        value = Decimal(str(raw_value).strip())
    except (TypeError, ValueError, InvalidOperation):
        errors.append(f"{field_label} must be a valid number.")
        return None

    if value <= 0:
        errors.append(f"{field_label} must be greater than zero.")
        return None
    return value


def _format_quantity(value):
    if isinstance(value, Decimal):
        normalized = value.normalize()
        text = format(normalized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _save_bom_template(item: Item, component_entries, *, replace_existing=False):
    """Persist a BOM template for a finished good item."""

    template = BillOfMaterial.query.filter_by(item_id=item.id).first()
    created = False
    replaced = False

    if template and not replace_existing:
        return template, created, replaced

    if template is None:
        template = BillOfMaterial(item=item)
        db.session.add(template)
        created = True
    else:
        template.components.clear()
        db.session.flush()
        replaced = True

    for entry in component_entries:
        template.components.append(
            BillOfMaterialComponent(
                component_item_id=entry["item"].id,
                quantity=entry["quantity"],
            )
        )

    return template, created, (created or replaced)


def _normalize_csv_key(field_name: str) -> str:
    if not field_name:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", field_name.strip().lower()).strip("_")


def _resolve_csv_field(normalized_fields, override_name, fallback_keys, *, label):
    if override_name:
        normalized_override = _normalize_csv_key(override_name)
        if normalized_override in normalized_fields:
            return normalized_fields[normalized_override], None
        return None, f"CSV does not include a column named '{override_name}' for {label}."

    for key in fallback_keys:
        if key in normalized_fields:
            return normalized_fields[key], None

    return None, None


def _parse_bulk_bom_rows(reader: csv.DictReader, *, column_overrides=None):
    if column_overrides is None:
        column_overrides = {}

    errors = []
    normalized_fields = {
        _normalize_csv_key(name): name for name in (reader.fieldnames or []) if name
    }

    assembly_field, assembly_error = _resolve_csv_field(
        normalized_fields,
        column_overrides.get("assembly"),
        ["assembly"],
        label="Assembly",
    )
    component_field, component_error = _resolve_csv_field(
        normalized_fields,
        column_overrides.get("component"),
        ["component", "component_sku"],
        label="Component",
    )
    quantity_field, quantity_error = _resolve_csv_field(
        normalized_fields,
        column_overrides.get("quantity"),
        ["component_qty", "component_quantity", "componentqty"],
        label="Component Qty",
    )
    level_field, level_error = _resolve_csv_field(
        normalized_fields,
        column_overrides.get("level"),
        ["level"],
        label="Level",
    )

    for field_error in (assembly_error, component_error, quantity_error, level_error):
        if field_error:
            errors.append(field_error)

    if not assembly_field or not component_field or not quantity_field:
        errors.append(
            "CSV must include columns for Assembly, Component, and Component Qty."
        )
        return {}, errors

    bom_rows = defaultdict(lambda: defaultdict(Decimal))
    current_assembly = None

    for row_index, row in enumerate(reader, start=2):
        assembly_value = (row.get(assembly_field) or "").strip()
        if assembly_value:
            current_assembly = assembly_value

        if not current_assembly:
            meaningful_values = [
                (value or "").strip() for value in row.values() if value is not None
            ]
            if not any(meaningful_values):
                continue
            errors.append(f"Row {row_index}: Assembly value is required.")
            continue

        component_value = (row.get(component_field) or "").strip()
        quantity_value = (row.get(quantity_field) or "").strip()
        level_value = (row.get(level_field) or "").strip() if level_field else ""

        if not component_value and not quantity_value:
            continue

        try:
            level_int = int(level_value) if level_value else None
        except ValueError:
            level_int = None

        if level_int == 0 and not component_value:
            continue

        if not component_value:
            errors.append(
                f"Row {row_index}: Component value is required for assembly {current_assembly}."
            )
            continue

        if not quantity_value:
            errors.append(
                f"Row {row_index}: Component quantity is required for assembly {current_assembly}."
            )
            continue

        try:
            quantity = _parse_positive_quantity(quantity_value, allow_zero=True)
        except (TypeError, ValueError, InvalidOperation):
            errors.append(
                f"Row {row_index}: Component quantity for {component_value} must be a positive number."
            )
            continue
        if quantity == 0:
            continue

        bom_rows[current_assembly][component_value] += quantity

    if not bom_rows and not errors:
        errors.append("No BOM component rows were found in the CSV file.")

    return bom_rows, errors


def _prepare_order_detail(
    order: Order,
    *,
    pending_completed_ids=None,
    selected_batches=None,
    inspection_values=None,
):
    if selected_batches is None:
        selected_batches = {}

    gate_detail = order.gate_details
    inspection_completed = False
    default_inspection = {
        "panel_count": "",
        "gate_height": "",
        "al_color": "",
        "insert_color": "",
        "lead_post_direction": "",
        "visi_panels": "",
    }
    if gate_detail is not None:
        inspection_completed = bool(gate_detail.inspection_recorded_at)
        default_inspection.update(
            {
                "panel_count": (
                    "" if gate_detail.inspection_panel_count is None else gate_detail.inspection_panel_count
                ),
                "gate_height": (
                    ""
                    if gate_detail.inspection_gate_height is None
                    else gate_detail.inspection_gate_height
                ),
                "al_color": gate_detail.inspection_al_color or "",
                "insert_color": gate_detail.inspection_insert_color or "",
                "lead_post_direction": gate_detail.inspection_lead_post_direction or "",
                "visi_panels": gate_detail.inspection_visi_panels or "",
            }
        )
    inspection_entries = {**default_inspection, **(inspection_values or {})}

    component_options = {}
    component_requirements = {}
    component_consumptions = {}

    for step in order.routing_steps:
        for usage in step.component_usages:
            component_item = usage.bom_component.component_item
            component_options[usage.id] = _inventory_options(component_item.id)
            component_requirements[usage.id] = _component_requirement(usage)
            consumption_rows = []
            for consumption in usage.consumptions:
                movement = consumption.movement
                batch = movement.batch
                location = movement.location
                batch_label = batch.lot_number if batch else "Unbatched"
                location_label = location.code if location else "Unknown"
                consumption_rows.append(
                    {
                        "batch_label": batch_label,
                        "location_label": location_label,
                        "quantity": consumption.quantity,
                    }
                )
            component_consumptions[usage.id] = consumption_rows

    return {
        "order": order,
        "component_options": component_options,
        "component_requirements": component_requirements,
        "component_consumptions": component_consumptions,
        "pending_completed_ids": pending_completed_ids,
        "selected_batches": selected_batches,
        "inspection_values": inspection_entries,
        "inspection_completed": inspection_completed,
        "inspection_recorded_at": gate_detail.inspection_recorded_at if gate_detail else None,
    }


def _adjust_reservation(order_line: OrderLine, item_id: int, delta: int):
    """Adjust a reservation quantity for an order line and component item."""

    reservation = next(
        (res for res in order_line.reservations if res.item_id == item_id),
        None,
    )

    delta_value = Decimal(delta)

    if reservation:
        new_quantity = Decimal(reservation.quantity) + delta_value
        if new_quantity <= 0:
            db.session.delete(reservation)
        else:
            reservation.quantity = new_quantity
    elif delta_value > 0:
        db.session.add(
            Reservation(order_line=order_line, item_id=item_id, quantity=delta_value)
        )


@bp.route("/")
def orders_home():
    search_term = request.args.get("q", "").strip()
    customer_filter = request.args.get("customer", "").strip()
    query = Order.query.options(
        joinedload(Order.order_lines).joinedload(OrderLine.item),
        joinedload(Order.routing_steps),
        joinedload(Order.gate_details),
    ).filter(Order.status.in_(OrderStatus.ACTIVE_STATES))
    query = _search_filter(query, search_term)
    if customer_filter:
        query = query.filter(Order.customer_name.ilike(f"%{customer_filter}%"))
    open_orders = query.order_by(
        *_overdue_ordering(),
        Order.priority,
        Order.promised_date.is_(None),
        Order.promised_date,
        Order.order_number,
    ).all()
    return render_template(
        "orders/home.html",
        orders=open_orders,
        search_term=search_term,
        customer_filter=customer_filter,
        today=date.today(),
    )


@bp.route("/import", methods=["GET", "POST"])
@require_roles("admin")
def import_orders():
    preview_rows = []
    results = None
    errors: list[str] = []
    missing_columns: list[str] = []
    warnings: list[dict[str, str]] = []
    error_report = None

    if request.method == "POST":
        action = (request.form.get("action") or "preview").lower()
        upload = request.files.get("orders_file")
        if upload is None or not upload.filename:
            errors.append("Please select a CSV or Excel file to import.")
        else:
            mapped_rows, file_errors, missing_columns = _load_mapped_import_rows(upload)
            errors.extend(file_errors)

            if missing_columns:
                errors.append(
                    "Missing required columns: " + ", ".join(sorted(set(missing_columns)))
                )

            if not errors and mapped_rows:
                preview_rows = mapped_rows[:25]
                if action == "import":
                    results = _perform_order_import(mapped_rows, upload.filename)
                    error_report = _generate_error_report(results["errors"])
                    warnings = results.get("warnings", [])
                    flash(
                        "Import complete: "
                        f"{results['created']} created, "
                        f"{results['updated']} updated, "
                        f"{results['skipped']} skipped.",
                        "success",
                    )
            elif not errors:
                errors.append("The uploaded file did not contain any data rows.")

        for message in errors:
            flash(message, "danger")
        for entry in warnings:
            flash(f"Row {entry['row']}: {entry['message']}", "warning")

    return render_template(
        "orders/import_orders.html",
        preview_rows=preview_rows,
        results=results,
        display_columns=IMPORT_DISPLAY_COLUMNS,
        error_report=error_report,
    )


@bp.route("/priority", methods=["GET", "POST"])
@require_roles("admin")
def prioritize_orders():
    reorderable_statuses = OrderStatus.RESERVABLE_STATES

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        order_ids = payload.get("order_ids")

        if not isinstance(order_ids, list) or not order_ids:
            return (
                jsonify({"error": "Provide a list of order ids in priority order."}),
                400,
            )

        try:
            normalized_ids = [int(order_id) for order_id in order_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "Order ids must be integers."}), 400

        orders = (
            Order.query.filter(Order.id.in_(normalized_ids))
            .filter(Order.status.in_(reorderable_statuses))
            .all()
        )
        order_lookup = {order.id: order for order in orders}

        missing_ids = [order_id for order_id in normalized_ids if order_id not in order_lookup]
        if missing_ids:
            return (
                jsonify(
                    {
                        "error": "Some orders could not be reprioritized.",
                        "missing": missing_ids,
                    }
                ),
                400,
            )

        for new_priority, order_id in enumerate(normalized_ids, start=1):
            order_lookup[order_id].priority = new_priority

        db.session.commit()
        return jsonify({"updated": len(order_ids)})

    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.gate_details),
        )
        .filter(Order.status.in_(reorderable_statuses))
        .order_by(
            Order.priority,
            Order.promised_date.is_(None),
            Order.promised_date,
            Order.order_number,
        )
        .all()
    )

    return render_template("orders/priority.html", orders=orders)


@bp.route("/schedule")
def schedule_view():
    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.routing_steps),
        )
        .filter(Order.status.in_(OrderStatus.ACTIVE_STATES))
        .order_by(
            Order.priority,
            Order.scheduled_completion_date.is_(None),
            Order.scheduled_completion_date,
            Order.order_number,
        )
        .all()
    )

    type_totals = defaultdict(lambda: defaultdict(int))
    work_cell_totals = defaultdict(lambda: defaultdict(int))

    for order in orders:
        primary_line = order.primary_line
        if not primary_line:
            continue

        schedule_date = (
            primary_line.scheduled_completion_date or order.scheduled_completion_date
        )
        quantity = int(primary_line.quantity or 0)
        if quantity <= 0:
            continue

        item_type = ""
        if primary_line.item and primary_line.item.type:
            item_type = primary_line.item.type.strip()
        type_label = item_type or "Uncategorized"
        type_totals[schedule_date][type_label] += quantity

        work_cells = {
            (step.work_cell or "").strip()
            for step in order.routing_steps
            if (step.work_cell or "").strip()
        }
        if not work_cells:
            work_cells = {"Unassigned"}
        for work_cell in sorted(work_cells):
            work_cell_totals[schedule_date][work_cell] += quantity

    schedule_breakdowns = {
        "item_type": {
            "label": "By Item Type",
            "data": _format_schedule_breakdown(type_totals),
        },
        "work_cell": {
            "label": "By Work Cell",
            "data": _format_schedule_breakdown(work_cell_totals),
        },
    }

    return render_template(
        "orders/schedule.html",
        schedule_breakdowns=schedule_breakdowns,
        schedule_default="item_type",
    )


@bp.route("/open")
def view_open_orders():
    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.gate_details),
        )
        .filter(Order.status.in_(OrderStatus.RESERVABLE_STATES))
        .order_by(
            *_overdue_ordering(),
            Order.priority,
            Order.promised_date.is_(None),
            Order.promised_date,
            Order.order_number,
        )
        .all()
    )
    return render_template("orders/open.html", orders=orders, today=date.today())


@bp.route("/closed")
def view_closed_orders():
    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.gate_details),
        )
        .filter(Order.status == OrderStatus.CLOSED)
        .order_by(
            *_overdue_ordering(),
            Order.priority,
            Order.promised_date.is_(None),
            Order.promised_date,
            Order.order_number,
        )
        .all()
    )
    return render_template("orders/closed.html", orders=orders, today=date.today())


@bp.route("/waiting")
@require_roles("admin")
def view_waiting_orders():
    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.gate_details),
        )
        .filter(Order.status == OrderStatus.WAITING_MATERIAL)
        .order_by(
            *_overdue_ordering(),
            Order.priority,
            Order.promised_date.is_(None),
            Order.promised_date,
            Order.order_number,
        )
        .all()
    )
    return render_template("orders/waiting.html", orders=orders, today=date.today())


@bp.route("/bom-template/<string:sku>")
@require_roles("admin")
def fetch_bom_template(sku: str):
    normalized_sku = (sku or "").strip()
    if not normalized_sku:
        return jsonify({"error": "Finished good part number is required."}), 400

    item = Item.query.filter_by(sku=normalized_sku).first()
    if item is None:
        return (
            jsonify(
                {"error": f"Finished good part number '{normalized_sku}' was not found."}
            ),
            404,
        )

    bom = (
        BillOfMaterial.query.options(
            joinedload(BillOfMaterial.components)
            .joinedload(BillOfMaterialComponent.component_item)
        )
        .filter_by(item_id=item.id)
        .first()
    )
    if bom is None:
        return (
            jsonify(
                {
                    "error": f"No BOM template stored for {item.sku}.",
                    "item": {"sku": item.sku, "name": item.name},
                }
            ),
            404,
        )

    components = [
        {
            "sku": component.component_item.sku,
            "name": component.component_item.name,
            "quantity": float(component.quantity),
        }
        for component in sorted(
            bom.components, key=lambda entry: entry.component_item.sku
        )
    ]
    return jsonify(
        {
            "item": {"sku": item.sku, "name": item.name},
            "components": components,
            "updated_at": bom.updated_at.isoformat() if bom.updated_at else None,
        }
    )


def _parse_date(raw_value, field_label, errors):
    if not raw_value:
        errors.append(f"{field_label} is required.")
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{field_label} must be a valid date (YYYY-MM-DD).")
        return None


@bp.route("/bom-library", methods=["GET", "POST"])
@require_roles("admin")
def bom_library():
    items = Item.query.order_by(Item.sku).all()
    templates = (
        BillOfMaterial.query.options(
            joinedload(BillOfMaterial.item),
            joinedload(BillOfMaterial.components).joinedload(
                BillOfMaterialComponent.component_item
            ),
        )
        .order_by(BillOfMaterial.updated_at.desc())
        .all()
    )

    form_data = {"finished_good_sku": "", "bom": []}
    editing_template = None
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip()
        errors = []
        finished_good_sku = (request.form.get("finished_good_sku") or "").strip()
        form_data["finished_good_sku"] = finished_good_sku

        if action == "delete":
            if not finished_good_sku:
                flash("Select a finished good to delete its BOM template.", "danger")
                return redirect(url_for("orders.bom_library"))

            template = (
                BillOfMaterial.query.options(joinedload(BillOfMaterial.item))
                .join(Item)
                .filter(Item.sku == finished_good_sku)
                .first()
            )
            if template is None:
                flash(
                    f"No BOM template was found for finished good {finished_good_sku}.",
                    "warning",
                )
                return redirect(url_for("orders.bom_library"))

            db.session.delete(template)
            db.session.commit()
            flash(
                f"BOM template for {template.item.sku} was deleted successfully.",
                "success",
            )
            return redirect(url_for("orders.bom_library"))

        bom_payload = []
        if action == "import_csv":
            upload = request.files.get("csv_file")
            if upload is None or not upload.filename:
                errors.append("A CSV file is required to import a BOM.")
            else:
                try:
                    raw_content = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    errors.append("CSV import files must be UTF-8 encoded.")
                else:
                    stream = io.StringIO(raw_content)
                    reader = csv.DictReader(stream)
                    if not reader.fieldnames:
                        errors.append("CSV file is empty.")
                    else:
                        normalized = {
                            (name or "").strip().lower(): name
                            for name in reader.fieldnames
                        }
                        sku_field = normalized.get("component_sku") or normalized.get("sku")
                        quantity_field = normalized.get("quantity")
                        if not sku_field or not quantity_field:
                            errors.append(
                                "CSV must include 'component_sku' and 'quantity' columns."
                            )
                        else:
                            parsed_entries = []
                            for row in reader:
                                sku_value = (row.get(sku_field) or "").strip()
                                quantity_value = (row.get(quantity_field) or "").strip()
                                if not sku_value and not quantity_value:
                                    continue
                                parsed_entries.append(
                                    {"sku": sku_value, "quantity": quantity_value}
                                )
                            if not parsed_entries:
                                errors.append(
                                    "CSV did not include any component rows to import."
                                )
                            bom_payload = parsed_entries
                            form_data["bom"] = parsed_entries
        else:
            bom_raw = request.form.get("bom_data") or "[]"
            try:
                bom_payload = json.loads(bom_raw)
                if not isinstance(bom_payload, list):
                    raise ValueError
            except ValueError:
                bom_payload = []
                errors.append(
                    "Unable to read the BOM component details submitted for the template."
                )
            form_data["bom"] = bom_payload

        finished_good = None
        if not finished_good_sku:
            errors.append("Finished good part number is required.")
        else:
            finished_good = Item.query.filter_by(sku=finished_good_sku).first()
            if finished_good is None:
                errors.append(
                    f"Finished good part number '{finished_good_sku}' was not found."
                )
            else:
                editing_template = (
                    BillOfMaterial.query.options(
                        joinedload(BillOfMaterial.components).joinedload(
                            BillOfMaterialComponent.component_item
                        )
                    )
                    .filter_by(item_id=finished_good.id)
                    .first()
                )

        bom_components = []
        component_skus_seen = set()
        if not bom_payload:
            errors.append("At least one BOM component is required to save a template.")
        else:
            for entry in bom_payload:
                if not isinstance(entry, dict):
                    errors.append("Each BOM component must include a SKU and quantity.")
                    continue
                sku = (entry.get("sku") or "").strip()
                quantity_value = entry.get("quantity")
                if not sku:
                    errors.append("BOM components require a component SKU.")
                    continue
                if sku in component_skus_seen:
                    errors.append(f"BOM component {sku} is listed more than once.")
                    continue
                component_item = Item.query.filter_by(sku=sku).first()
                if component_item is None:
                    errors.append(f"BOM component SKU '{sku}' was not found.")
                    continue
                try:
                    component_quantity = _parse_positive_quantity(quantity_value)
                except (TypeError, ValueError, InvalidOperation):
                    errors.append(
                        f"BOM component quantity for {sku} must be a positive number."
                    )
                    continue
                bom_components.append(
                    {"sku": sku, "item": component_item, "quantity": component_quantity}
                )
                component_skus_seen.add(sku)

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "orders/bom_library.html",
                items=items,
                templates=templates,
                form_data=form_data,
            )

        _, created, changed = _save_bom_template(
            finished_good, bom_components, replace_existing=True
        )
        db.session.commit()
        if created:
            flash(
                f"BOM template for {finished_good.sku} was created successfully.",
                "success",
            )
        elif changed:
            flash(
                f"BOM template for {finished_good.sku} was updated successfully.",
                "success",
            )
        else:
            flash(
                f"BOM template for {finished_good.sku} already exists and was not modified.",
                "info",
            )
        return redirect(url_for("orders.bom_library"))
    else:
        editing_sku = (request.args.get("sku") or "").strip()
        if editing_sku:
            editing_template = (
                BillOfMaterial.query.options(
                    joinedload(BillOfMaterial.item),
                    joinedload(BillOfMaterial.components).joinedload(
                        BillOfMaterialComponent.component_item
                    ),
                )
                .join(Item)
                .filter(Item.sku == editing_sku)
                .first()
            )
            if editing_template:
                form_data["finished_good_sku"] = editing_template.item.sku
                form_data["bom"] = [
                    {
                        "sku": component.component_item.sku,
                        "quantity": float(component.quantity),
                    }
                    for component in editing_template.components
                ]
            else:
                flash(
                    f"No BOM template was found for finished good {editing_sku}.",
                    "warning",
                )

    return render_template(
        "orders/bom_library.html",
        items=items,
        templates=templates,
        form_data=form_data,
        editing_template=editing_template,
    )


@bp.route("/bom-bulk-import", methods=["GET", "POST"])
@require_roles("admin")
def bom_bulk_import():
    import_results = None
    warnings: list[str] = []

    if request.method == "POST":
        errors = []
        upload = request.files.get("csv_file")
        bom_rows = {}
        item_lookup = {}
        column_overrides = {
            "assembly": request.form.get("assembly_column", "").strip() or None,
            "component": request.form.get("component_column", "").strip() or None,
            "quantity": request.form.get("quantity_column", "").strip() or None,
            "level": request.form.get("level_column", "").strip() or None,
        }

        assembly_missing_handling = (
            request.form.get("missing_assembly_handling") or "skip"
        ).lower()
        component_missing_handling = (
            request.form.get("missing_component_handling") or "skip"
        ).lower()
        missing_handling_choices = {"skip", "abort"}
        if assembly_missing_handling not in missing_handling_choices:
            assembly_missing_handling = "skip"
        if component_missing_handling not in missing_handling_choices:
            component_missing_handling = "skip"

        if upload is None or not upload.filename:
            errors.append("A CSV file is required to import BOM templates.")
        else:
            try:
                raw_content = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                errors.append("CSV import files must be UTF-8 encoded.")
            else:
                stream = io.StringIO(raw_content)
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    errors.append("CSV file is empty.")
                else:
                    bom_rows, parse_errors = _parse_bulk_bom_rows(
                        reader, column_overrides=column_overrides
                    )
                    errors.extend(parse_errors)

        if not errors and bom_rows:
            assembly_skus = set(bom_rows.keys())
            component_skus = {
                component
                for components in bom_rows.values()
                for component in components.keys()
            }
            required_skus = assembly_skus | component_skus
            items = (
                Item.query.filter(Item.sku.in_(required_skus)).all()
                if required_skus
                else []
            )
            item_lookup = {item.sku: item for item in items}

            missing_assemblies = sorted(
                sku for sku in assembly_skus if sku not in item_lookup
            )
            missing_components = sorted(
                sku for sku in component_skus if sku not in item_lookup
            )

            import_cancelled = False

            if missing_assemblies:
                if assembly_missing_handling == "abort":
                    import_cancelled = True
                    warnings.append(
                        "Import cancelled because required assemblies are missing: "
                        + ", ".join(missing_assemblies)
                    )
                else:
                    for missing_sku in missing_assemblies:
                        bom_rows.pop(missing_sku, None)
                    warnings.append(
                        "Skipped missing assemblies: " + ", ".join(missing_assemblies)
                    )

            if missing_components and not import_cancelled:
                if component_missing_handling == "abort":
                    import_cancelled = True
                    warnings.append(
                        "Import cancelled because required components are missing: "
                        + ", ".join(missing_components)
                    )
                else:
                    removed_components = 0
                    assemblies_with_removed_components = set()

                    for assembly_sku, components in list(bom_rows.items()):
                        for component_sku in list(components.keys()):
                            if component_sku in missing_components:
                                removed_components += 1
                                components.pop(component_sku, None)
                                assemblies_with_removed_components.add(assembly_sku)

                        if not components:
                            bom_rows.pop(assembly_sku, None)

                    if removed_components:
                        warnings.append(
                            "Skipped missing components: " + ", ".join(missing_components)
                        )
                    if assemblies_with_removed_components:
                        warnings.append(
                            "Removed empty BOM rows after filtering missing components for: "
                            + ", ".join(sorted(assemblies_with_removed_components))
                        )

            if import_cancelled:
                bom_rows = {}

        if not errors and bom_rows:
            import_details = []
            created_count = 0
            updated_count = 0

            for assembly_sku in sorted(bom_rows.keys()):
                item = item_lookup[assembly_sku]
                component_entries = [
                    {"item": item_lookup[component_sku], "quantity": quantity}
                    for component_sku, quantity in sorted(
                        bom_rows[assembly_sku].items()
                    )
                ]

                template, created, changed = _save_bom_template(
                    item, component_entries, replace_existing=True
                )
                import_details.append(
                    {
                        "sku": assembly_sku,
                        "component_count": len(component_entries),
                        "created": created,
                        "updated": (not created and changed),
                    }
                )
                if created:
                    created_count += 1
                elif changed:
                    updated_count += 1

            if import_details:
                db.session.commit()

                unchanged_count = len(import_details) - created_count - updated_count
                import_results = {
                    "total": len(import_details),
                    "created": created_count,
                    "updated": updated_count,
                    "unchanged": unchanged_count,
                    "details": import_details,
                }

                flash(
                    "Bulk import complete: "
                    f"{created_count} created, {updated_count} updated, {unchanged_count} unchanged.",
                    "success",
                )
            else:
                warnings.append(
                    "No BOM templates were imported because all rows were skipped or removed."
                )
        else:
            for error in errors:
                flash(error, "danger")

        for warning in warnings:
            flash(warning, "warning")

    return render_template(
        "orders/bom_bulk_import.html",
        import_results=import_results,
    )


@bp.route("/new", methods=["GET", "POST"])
@require_roles("admin")
def new_order():
    form_data = {
        "order_number": "",
        "purchase_order_number": "",
        "customer_name": "",
        "created_by": "",
        "general_notes": "",
        "promised_date": "",
        "scheduled_ship_date": "",
        "order_type": ORDER_TYPE_CHOICES[0],
        "priority": "0",
        "item_number": "",
        "production_quantity": "",
        "panel_count": "",
        "total_gate_height": "",
        "al_color": "",
        "insert_color": "",
        "lead_post_direction": "",
        "visi_panels": "",
        "half_panel_color": "",
        "hardware_option": "",
        "adders": "",
    }

    if request.method == "POST":
        errors = []
        order_number = (request.form.get("order_number") or "").strip()
        purchase_order_number = (request.form.get("purchase_order_number") or "").strip()
        customer_name = (request.form.get("customer_name") or "").strip()
        created_by = (request.form.get("created_by") or "").strip()
        general_notes = request.form.get("general_notes") or ""
        general_notes_db_value = general_notes if general_notes.strip() else None
        promised_date_raw = (request.form.get("promised_date") or "").strip()
        scheduled_ship_raw = (request.form.get("scheduled_ship_date") or "").strip()
        order_type = request.form.get("order_type") or ORDER_TYPE_CHOICES[0]
        priority_raw = request.form.get("priority") or "0"

        form_data.update(
            {
                "order_number": order_number,
                "purchase_order_number": purchase_order_number,
                "customer_name": customer_name,
                "created_by": created_by,
                "general_notes": general_notes,
                "promised_date": promised_date_raw,
                "scheduled_ship_date": scheduled_ship_raw,
                "order_type": order_type,
                "priority": priority_raw,
            }
        )

        if not order_number:
            errors.append("Production Order Number is required.")
        elif Order.query.filter_by(order_number=order_number).first():
            errors.append("Production Order Number already exists.")

        if not purchase_order_number:
            errors.append("Purchase Order Number is required.")

        if not customer_name:
            errors.append("Customer Name is required.")

        if not created_by:
            errors.append("Order Created By is required.")

        if order_type not in ORDER_TYPE_CHOICES:
            errors.append("Select a valid order type.")

        promised_date = _parse_date(promised_date_raw, "Promise Date", errors)
        scheduled_ship_date = _parse_date(
            scheduled_ship_raw, "Scheduled Ship Date", errors
        )

        priority_value = None
        try:
            priority_value = int(str(priority_raw).strip())
        except (TypeError, ValueError):
            errors.append("Priority must be a whole number.")

        gate_detail = None
        if order_type == "Gates":
            gate_detail = {
                "item_number": (request.form.get("item_number") or "").strip(),
                "production_quantity": request.form.get("production_quantity"),
                "panel_count": request.form.get("panel_count"),
                "total_gate_height": request.form.get("total_gate_height"),
                "al_color": (request.form.get("al_color") or "").strip(),
                "insert_color": (request.form.get("insert_color") or "").strip(),
                "lead_post_direction": (request.form.get("lead_post_direction") or "").strip(),
                "visi_panels": (request.form.get("visi_panels") or "").strip(),
                "half_panel_color": (request.form.get("half_panel_color") or "").strip(),
                "hardware_option": (request.form.get("hardware_option") or "").strip(),
                "adders": (request.form.get("adders") or "").strip(),
            }
            form_data.update(gate_detail)

            if not gate_detail["item_number"]:
                errors.append("Item Number is required for gate orders.")

            production_quantity = _parse_positive_int(
                gate_detail["production_quantity"], "Production Quantity", errors
            )
            panel_count = _parse_positive_int(
                gate_detail["panel_count"], "Panel Count", errors
            )
            total_gate_height = _parse_decimal(
                gate_detail["total_gate_height"], "Total Gate Height", errors
            )

            if not gate_detail["al_color"]:
                errors.append("AL Color is required for gate orders.")
            if not gate_detail["insert_color"]:
                errors.append("Acrylic/Wood/Vinyl Color is required for gate orders.")
            if not gate_detail["lead_post_direction"]:
                errors.append("Lead Post Direction is required for gate orders.")
            if not gate_detail["visi_panels"]:
                errors.append("Visi Panels selection is required for gate orders.")
            if not gate_detail["half_panel_color"]:
                errors.append("1/2 Panel Color is required for gate orders.")
        else:
            production_quantity = None
            panel_count = None
            total_gate_height = None

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "orders/new.html",
                form_data=form_data,
                order_type_choices=ORDER_TYPE_CHOICES,
            )

        today = datetime.utcnow().date()
        order_status = OrderStatus.OPEN
        if scheduled_ship_date and scheduled_ship_date > today:
            order_status = OrderStatus.SCHEDULED

        order = Order(
            order_number=order_number,
            order_type=order_type,
            purchase_order_number=purchase_order_number,
            customer_name=customer_name,
            created_by=created_by,
            general_notes=general_notes_db_value,
            promised_date=promised_date,
            scheduled_ship_date=scheduled_ship_date,
            scheduled_completion_date=scheduled_ship_date,
            status=order_status,
            priority=priority_value or 0,
        )

        db.session.add(order)

        if order_type == "Gates":
            db.session.add(
                GateOrderDetail(
                    order=order,
                    item_number=gate_detail["item_number"],
                    production_quantity=production_quantity,
                    panel_count=panel_count,
                    total_gate_height=total_gate_height,
                    al_color=gate_detail["al_color"],
                    insert_color=gate_detail["insert_color"],
                    lead_post_direction=gate_detail["lead_post_direction"],
                    visi_panels=gate_detail["visi_panels"],
                    half_panel_color=gate_detail["half_panel_color"],
                    hardware_option=gate_detail["hardware_option"] or None,
                    adders=gate_detail["adders"] or None,
                )
            )

            for sequence, step_name in enumerate(GATE_ROUTING_STEPS, start=1):
                db.session.add(
                    RoutingStep(
                        order=order,
                        sequence=sequence,
                        work_cell=step_name,
                        description=f"{step_name} step",
                    )
                )

        db.session.commit()
        flash("Order created", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template(
        "orders/new.html",
        form_data=form_data,
        order_type_choices=ORDER_TYPE_CHOICES,
    )

@bp.route("/<int:order_id>")
def view_order(order_id):
    order = (
        Order.query.options(
            joinedload(Order.order_lines)
            .joinedload(OrderLine.components)
            .joinedload(OrderComponent.component_item),
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.order_lines)
            .joinedload(OrderLine.reservations)
            .joinedload(Reservation.item),
            joinedload(Order.routing_steps)
            .joinedload(RoutingStep.component_links)
            .joinedload(RoutingStepComponent.order_component)
            .joinedload(OrderComponent.component_item),
            joinedload(Order.gate_details),
        )
        .filter_by(id=order_id)
        .first_or_404()
    )
    context = _prepare_order_detail(order)
    return render_template("orders/view.html", **context)


@bp.route("/<int:order_id>/notes", methods=["POST"])
def add_order_note(order_id):
    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    order = Order.query.filter_by(id=order_id).first_or_404()
    new_note = (request.form.get("new_note") or "").strip()

    if not new_note:
        flash("Please enter a note before adding it to the order.", "warning")
        return redirect(url_for("orders.view_order", order_id=order.id))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    note_entry = f"[{timestamp}] {new_note}"

    if order.general_notes:
        order.general_notes = f"{order.general_notes.strip()}\n{note_entry}"
    else:
        order.general_notes = note_entry

    db.session.commit()
    flash("Note added to the order.", "success")

    return redirect(url_for("orders.view_order", order_id=order.id))


@bp.route("/<int:order_id>/inspection-report")
def inspection_report(order_id):
    order = (
        Order.query.options(joinedload(Order.gate_details))
        .filter_by(id=order_id)
        .first_or_404()
    )
    if order.gate_details is None:
        abort(404)

    return render_template(
        "orders/inspection_report.html",
        order=order,
        inspection=order.gate_details,
    )


@bp.route("/<int:order_id>/routing", methods=["POST"])
def update_routing(order_id):
    order = (
        Order.query.options(
            joinedload(Order.routing_steps)
            .joinedload(RoutingStep.component_usages)
            .joinedload(RoutingStepComponent.bom_component)
            .joinedload(OrderComponent.order_line)
            .joinedload(OrderLine.reservations),
            joinedload(Order.routing_steps)
            .joinedload(RoutingStep.component_usages)
            .joinedload(RoutingStepComponent.consumptions)
            .joinedload(RoutingStepConsumption.movement)
            .joinedload(Movement.batch),
            joinedload(Order.routing_steps)
            .joinedload(RoutingStep.component_usages)
            .joinedload(RoutingStepComponent.consumptions)
            .joinedload(RoutingStepConsumption.movement)
            .joinedload(Movement.location),
        )
        .filter_by(id=order_id)
        .first_or_404()
    )

    inspection_record = None
    selected_ids = set()
    for raw_id in request.form.getlist("completed_steps"):
        try:
            selected_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue

    selected_batches = {}
    for form_key, value in request.form.items():
        if not form_key.startswith("usage_"):
            continue
        try:
            usage_id = int(form_key.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        selected_batches[usage_id] = value

    inspection_values = {
        "panel_count": (request.form.get("inspection_panel_count") or "").strip(),
        "gate_height": (request.form.get("inspection_gate_height") or "").strip(),
        "al_color": (request.form.get("inspection_al_color") or "").strip(),
        "insert_color": (request.form.get("inspection_insert_color") or "").strip(),
        "lead_post_direction": (request.form.get("inspection_lead_post_direction") or "").strip(),
        "visi_panels": (request.form.get("inspection_visi_panels") or "").strip(),
    }

    errors = []
    planned_consumptions = defaultdict(list)

    for step in order.routing_steps:
        desired_state = step.id in selected_ids
        if desired_state and not step.completed:
            if step.work_cell == "Inspection" and order.gate_details is not None:
                gate_detail = order.gate_details
                inspection_errors_before = len(errors)
                inspection_record_candidate = {}

                try:
                    entered_panel_count = int(inspection_values["panel_count"])
                except (TypeError, ValueError):
                    errors.append(
                        "Enter a whole number for Panel Count to complete Inspection."
                    )
                    entered_panel_count = None

                if entered_panel_count is not None:
                    inspection_record_candidate["panel_count"] = entered_panel_count
                    if entered_panel_count != gate_detail.panel_count:
                        errors.append(
                            "Panel Count must match the order to complete Inspection."
                        )

                try:
                    entered_height = Decimal(inspection_values["gate_height"])
                except (InvalidOperation, TypeError):
                    errors.append(
                        "Enter a valid number for Gate Height to complete Inspection."
                    )
                    entered_height = None

                if entered_height is not None:
                    inspection_record_candidate["gate_height"] = entered_height
                    expected_height = Decimal(gate_detail.total_gate_height)
                    if abs(entered_height - expected_height) > Decimal("0.125"):
                        errors.append(
                            "Gate Height must be within ±0.125 of the order to complete Inspection."
                        )

                def _matches(expected: str, provided_key: str, label: str):
                    provided = inspection_values[provided_key]
                    if not provided:
                        errors.append(
                            f"Provide {label} to complete Inspection."
                        )
                        return
                    cleaned = provided.strip()
                    if cleaned.casefold() != str(expected).strip().casefold():
                        errors.append(
                            f"{label} must match the order to complete Inspection."
                        )
                        return
                    inspection_record_candidate[provided_key] = cleaned

                _matches(gate_detail.al_color, "al_color", "AL Color")
                _matches(
                    gate_detail.insert_color,
                    "insert_color",
                    "Acrylic/Wood/Vinyl Color",
                )
                _matches(
                    gate_detail.lead_post_direction,
                    "lead_post_direction",
                    "Lead Post Direction",
                )
                _matches(gate_detail.visi_panels, "visi_panels", "Visi Panels")

                if len(errors) == inspection_errors_before:
                    inspection_record = inspection_record_candidate

            for usage in step.component_usages:
                field_name = f"usage_{usage.id}"
                selection = (request.form.get(field_name) or "").strip()
                if not selection:
                    errors.append(
                        "Select a batch for "
                        f"{usage.bom_component.component_item.sku} on step {step.sequence}."
                    )
                    continue
                try:
                    batch_token, location_token = selection.split("::", 1)
                    batch_id = None if batch_token == "none" else int(batch_token)
                    location_id = int(location_token)
                except (ValueError, TypeError):
                    errors.append(
                        "Invalid batch selection for "
                        f"{usage.bom_component.component_item.sku} on step {step.sequence}."
                    )
                    continue

                required_qty = _component_requirement(usage)
                available_qty = _position_balance(
                    usage.bom_component.component_item_id, batch_id, location_id
                )
                if required_qty > available_qty:
                    errors.append(
                        f"Not enough stock in selected batch for "
                        f"{usage.bom_component.component_item.sku} on step {step.sequence}. "
                        f"Required {required_qty}, available {available_qty}."
                    )
                    continue

                planned_consumptions[step.id].append(
                    {
                        "usage": usage,
                        "batch_id": batch_id,
                        "location_id": location_id,
                        "quantity": required_qty,
                    }
                )

    if errors:
        for error in errors:
            flash(error, "danger")
        context = _prepare_order_detail(
            order,
            pending_completed_ids=selected_ids,
            selected_batches=selected_batches,
            inspection_values=inspection_values,
        )
        return render_template("orders/view.html", **context), 400

    changes_made = False
    current_time = datetime.utcnow()
    for step in order.routing_steps:
        desired_state = step.id in selected_ids
        if step.completed == desired_state:
            continue

        changes_made = True
        if desired_state:
            step.completed = True
            step.completed_at = current_time
            if (
                step.work_cell == "Inspection"
                and order.gate_details is not None
                and inspection_record is not None
            ):
                gate_detail = order.gate_details
                gate_detail.inspection_panel_count = inspection_record.get("panel_count")
                gate_detail.inspection_gate_height = inspection_record.get("gate_height")
                gate_detail.inspection_al_color = inspection_record.get("al_color")
                gate_detail.inspection_insert_color = inspection_record.get("insert_color")
                gate_detail.inspection_lead_post_direction = inspection_record.get(
                    "lead_post_direction"
                )
                gate_detail.inspection_visi_panels = inspection_record.get("visi_panels")
                gate_detail.inspection_recorded_at = current_time
            for action in planned_consumptions.get(step.id, []):
                usage = action["usage"]
                movement = Movement(
                    item_id=usage.bom_component.component_item_id,
                    batch_id=action["batch_id"],
                    location_id=action["location_id"],
                    quantity=-action["quantity"],
                    movement_type="ISSUE",
                    reference=f"Order {order.order_number} Step {step.sequence}",
                )
                db.session.add(movement)
                db.session.flush()
                db.session.add(
                    RoutingStepConsumption(
                        routing_step_component=usage,
                        movement=movement,
                        quantity=action["quantity"],
                    )
                )
                _adjust_reservation(
                    usage.bom_component.order_line,
                    usage.bom_component.component_item_id,
                    -action["quantity"],
                )
        else:
            for usage in step.component_usages:
                for consumption in list(usage.consumptions):
                    movement = consumption.movement
                    _adjust_reservation(
                        usage.bom_component.order_line,
                        usage.bom_component.component_item_id,
                        consumption.quantity,
                    )
                    if movement is not None:
                        db.session.delete(movement)
                    db.session.delete(consumption)
            step.completed = False
            step.completed_at = None

    all_steps_completed = (
        bool(order.routing_steps) and all(step.completed for step in order.routing_steps)
    )
    order_closed_now = False

    if all_steps_completed and order.status != OrderStatus.CLOSED:
        order.status = OrderStatus.CLOSED
        order_closed_now = True
    elif not all_steps_completed and order.status == OrderStatus.CLOSED:
        order.status = OrderStatus.SCHEDULED

    if changes_made or order_closed_now:
        db.session.flush()
        if order_closed_now:
            _rebalance_priorities()
        db.session.commit()
        flash(
            "Routing progress updated. Order completed and removed from prioritization."
            if order_closed_now
            else "Routing progress updated",
            "success",
        )
    else:
        flash("No routing updates were made.", "info")

    return redirect(url_for("orders.view_order", order_id=order.id))


@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
def edit_order(order_id):
    guard_response = _ensure_order_management_access()
    if guard_response is not None:
        return guard_response

    order = Order.query.get_or_404(order_id)
    status_choices = OrderStatus.ALL_STATUSES

    gate_detail = order.gate_details
    form_data = {
        "order_number": order.order_number,
        "purchase_order_number": order.purchase_order_number or "",
        "customer_name": order.customer_name or "",
        "created_by": order.created_by or "",
        "general_notes": order.general_notes or "",
        "promised_date": order.promised_date.isoformat() if order.promised_date else "",
        "scheduled_ship_date": order.scheduled_ship_date.isoformat() if order.scheduled_ship_date else "",
        "order_type": order.order_type,
        "priority": order.priority,
        "status": order.status,
        "item_number": gate_detail.item_number if gate_detail else "",
        "production_quantity": gate_detail.production_quantity if gate_detail else "",
        "panel_count": gate_detail.panel_count if gate_detail else "",
        "total_gate_height": gate_detail.total_gate_height if gate_detail else "",
        "al_color": gate_detail.al_color if gate_detail else "",
        "insert_color": gate_detail.insert_color if gate_detail else "",
        "lead_post_direction": gate_detail.lead_post_direction if gate_detail else "",
        "visi_panels": gate_detail.visi_panels if gate_detail else "",
        "half_panel_color": gate_detail.half_panel_color if gate_detail else "",
        "hardware_option": gate_detail.hardware_option if gate_detail else "",
        "adders": gate_detail.adders if gate_detail else "",
    }

    if request.method == "POST":
        errors = []
        order_number = (request.form.get("order_number") or "").strip()
        purchase_order_number = (request.form.get("purchase_order_number") or "").strip()
        customer_name = (request.form.get("customer_name") or "").strip()
        created_by = (request.form.get("created_by") or "").strip()
        general_notes = request.form.get("general_notes") or ""
        general_notes_db_value = general_notes if general_notes.strip() else None
        promised_date_raw = (request.form.get("promised_date") or "").strip()
        scheduled_ship_raw = (request.form.get("scheduled_ship_date") or "").strip()
        order_type = request.form.get("order_type") or order.order_type
        priority_raw = request.form.get("priority") or "0"
        status = request.form.get("status", order.status)

        form_data.update(
            {
                "order_number": order_number,
                "purchase_order_number": purchase_order_number,
                "customer_name": customer_name,
                "created_by": created_by,
                "general_notes": general_notes,
                "promised_date": promised_date_raw,
                "scheduled_ship_date": scheduled_ship_raw,
                "order_type": order_type,
                "priority": priority_raw,
                "status": status,
            }
        )

        if not order_number:
            errors.append("Production Order Number is required.")
        elif (
            order_number != order.order_number
            and Order.query.filter_by(order_number=order_number).first()
        ):
            errors.append("Production Order Number already exists.")

        if not purchase_order_number:
            errors.append("Purchase Order Number is required.")

        if not customer_name:
            errors.append("Customer Name is required.")

        if not created_by:
            errors.append("Order Created By is required.")

        if order_type not in ORDER_TYPE_CHOICES:
            errors.append("Select a valid order type.")

        promised_date = _parse_date(promised_date_raw, "Promise Date", errors)
        scheduled_ship_date = _parse_date(
            scheduled_ship_raw, "Scheduled Ship Date", errors
        )

        priority_value = None
        try:
            priority_value = int(str(priority_raw).strip())
        except (TypeError, ValueError):
            errors.append("Priority must be a whole number.")

        if status not in set(status_choices):
            errors.append("Invalid status")

        gate_detail_payload = None
        if order_type == "Gates":
            gate_detail_payload = {
                "item_number": (request.form.get("item_number") or "").strip(),
                "production_quantity": request.form.get("production_quantity"),
                "panel_count": request.form.get("panel_count"),
                "total_gate_height": request.form.get("total_gate_height"),
                "al_color": (request.form.get("al_color") or "").strip(),
                "insert_color": (request.form.get("insert_color") or "").strip(),
                "lead_post_direction": (request.form.get("lead_post_direction") or "").strip(),
                "visi_panels": (request.form.get("visi_panels") or "").strip(),
                "half_panel_color": (request.form.get("half_panel_color") or "").strip(),
                "hardware_option": (request.form.get("hardware_option") or "").strip(),
                "adders": (request.form.get("adders") or "").strip(),
            }
            form_data.update(gate_detail_payload)

            if not gate_detail_payload["item_number"]:
                errors.append("Item Number is required for gate orders.")

            production_quantity = _parse_positive_int(
                gate_detail_payload["production_quantity"],
                "Production Quantity",
                errors,
            )
            panel_count = _parse_positive_int(
                gate_detail_payload["panel_count"], "Panel Count", errors
            )
            total_gate_height = _parse_decimal(
                gate_detail_payload["total_gate_height"], "Total Gate Height", errors
            )

            if not gate_detail_payload["al_color"]:
                errors.append("AL Color is required for gate orders.")
            if not gate_detail_payload["insert_color"]:
                errors.append("Acrylic/Wood/Vinyl Color is required for gate orders.")
            if not gate_detail_payload["lead_post_direction"]:
                errors.append("Lead Post Direction is required for gate orders.")
            if not gate_detail_payload["visi_panels"]:
                errors.append("Visi Panels selection is required for gate orders.")
            if not gate_detail_payload["half_panel_color"]:
                errors.append("1/2 Panel Color is required for gate orders.")
        else:
            production_quantity = None
            panel_count = None
            total_gate_height = None

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "orders/edit.html",
                order=order,
                statuses=status_choices,
                status_labels=OrderStatus.LABELS,
                form_data=form_data,
                order_type_choices=ORDER_TYPE_CHOICES,
            )

        order.order_number = order_number
        order.order_type = order_type
        order.purchase_order_number = purchase_order_number
        order.customer_name = customer_name
        order.created_by = created_by
        order.general_notes = general_notes_db_value
        order.promised_date = promised_date
        order.scheduled_ship_date = scheduled_ship_date
        order.scheduled_completion_date = scheduled_ship_date
        order.priority = priority_value or 0
        order.status = status

        if order_type == "Gates":
            if gate_detail is None:
                gate_detail = GateOrderDetail(order=order)
                db.session.add(gate_detail)

            gate_detail.item_number = gate_detail_payload["item_number"]
            gate_detail.production_quantity = production_quantity
            gate_detail.panel_count = panel_count
            gate_detail.total_gate_height = total_gate_height
            gate_detail.al_color = gate_detail_payload["al_color"]
            gate_detail.insert_color = gate_detail_payload["insert_color"]
            gate_detail.lead_post_direction = gate_detail_payload["lead_post_direction"]
            gate_detail.visi_panels = gate_detail_payload["visi_panels"]
            gate_detail.half_panel_color = gate_detail_payload["half_panel_color"]
            gate_detail.hardware_option = gate_detail_payload["hardware_option"] or None
            gate_detail.adders = gate_detail_payload["adders"] or None

            if not order.routing_steps:
                for sequence, step_name in enumerate(GATE_ROUTING_STEPS, start=1):
                    db.session.add(
                        RoutingStep(
                            order=order,
                            sequence=sequence,
                            work_cell=step_name,
                            description=f"{step_name} step",
                        )
                    )
        else:
            if gate_detail is not None:
                db.session.delete(gate_detail)
                gate_detail = None

        db.session.commit()
        flash("Order updated", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template(
        "orders/edit.html",
        order=order,
        statuses=status_choices,
        status_labels=OrderStatus.LABELS,
        form_data=form_data,
        order_type_choices=ORDER_TYPE_CHOICES,
    )

@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete_order(order_id):
    guard_response = _ensure_order_management_access()
    if guard_response is not None:
        return guard_response

    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    flash("Order deleted", "success")
    return redirect(url_for("orders.orders_home"))
