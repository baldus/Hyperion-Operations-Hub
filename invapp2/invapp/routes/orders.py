import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.auth import blueprint_page_guard
from invapp.security import require_roles
from invapp.models import (
    BillOfMaterial,
    BillOfMaterialComponent,
    Batch,
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

bp = Blueprint("orders", __name__, url_prefix="/orders")

bp.before_request(blueprint_page_guard("orders"))


def _search_filter(query, search_term):
    if not search_term:
        return query

    like_term = f"%{search_term}%"
    return query.join(Order.order_lines).join(OrderLine.item).filter(
        or_(
            Order.order_number.ilike(like_term),
            Item.sku.ilike(like_term),
            Item.name.ilike(like_term),
        )
    )


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


def _prepare_order_detail(order: Order, *, pending_completed_ids=None, selected_batches=None):
    if selected_batches is None:
        selected_batches = {}

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
    query = Order.query.options(
        joinedload(Order.order_lines).joinedload(OrderLine.item),
        joinedload(Order.routing_steps),
    ).filter(Order.status.in_(OrderStatus.RESERVABLE_STATES))
    query = _search_filter(query, search_term)
    open_orders = query.order_by(Order.promised_date.is_(None), Order.promised_date, Order.order_number).all()
    return render_template("orders/home.html", orders=open_orders, search_term=search_term)


@bp.route("/schedule")
def schedule_view():
    orders = (
        Order.query.options(
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.routing_steps),
        )
        .filter(Order.status.in_(OrderStatus.ACTIVE_STATES))
        .order_by(Order.scheduled_completion_date.is_(None), Order.scheduled_completion_date, Order.order_number)
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
        Order.query.options(joinedload(Order.order_lines).joinedload(OrderLine.item))
        .filter(Order.status.in_(OrderStatus.RESERVABLE_STATES))
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/open.html", orders=orders)


@bp.route("/closed")
def view_closed_orders():
    orders = (
        Order.query.options(joinedload(Order.order_lines).joinedload(OrderLine.item))
        .filter(Order.status == OrderStatus.CLOSED)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/closed.html", orders=orders)


@bp.route("/waiting")
@require_roles("admin")
def view_waiting_orders():
    orders = (
        Order.query.options(joinedload(Order.order_lines).joinedload(OrderLine.item))
        .filter(Order.status == OrderStatus.WAITING_MATERIAL)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/waiting.html", orders=orders)


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
    items = Item.query.order_by(Item.sku).all()
    form_data = {
        "order_number": "",
        "finished_good_sku": "",
        "quantity": "",
        "customer_name": "",
        "created_by": "",
        "general_notes": "",
        "promised_date": "",
        "scheduled_start_date": "",
        "scheduled_completion_date": "",
        "bom": [],
        "steps": [],
        "save_bom_template": False,
    }

    if request.method == "POST":
        errors = []
        order_number = (request.form.get("order_number") or "").strip()
        finished_good_sku = (request.form.get("finished_good_sku") or "").strip()
        quantity_raw = (request.form.get("quantity") or "").strip()
        customer_name = (request.form.get("customer_name") or "").strip()
        created_by = (request.form.get("created_by") or "").strip()
        general_notes = request.form.get("general_notes") or ""
        general_notes_db_value = general_notes if general_notes.strip() else None
        promised_date_raw = (request.form.get("promised_date") or "").strip()
        scheduled_start_raw = (
            request.form.get("scheduled_start_date") or ""
        ).strip()
        scheduled_completion_raw = (
            request.form.get("scheduled_completion_date") or ""
        ).strip()
        bom_raw = request.form.get("bom_data") or "[]"
        routing_raw = request.form.get("routing_data") or "[]"
        save_bom_template = request.form.get("save_bom_template") == "1"

        form_data.update(
            {
                "order_number": order_number,
                "finished_good_sku": finished_good_sku,
                "quantity": quantity_raw,
                "customer_name": customer_name,
                "created_by": created_by,
                "general_notes": general_notes,
                "promised_date": promised_date_raw,
                "scheduled_start_date": scheduled_start_raw,
                "scheduled_completion_date": scheduled_completion_raw,
                "save_bom_template": save_bom_template,
            }
        )

        if not order_number:
            errors.append("Order number is required.")
        elif Order.query.filter_by(order_number=order_number).first():
            errors.append("Order number already exists.")

        if not customer_name:
            errors.append("Customer name is required.")

        if not created_by:
            errors.append("Order creator name is required.")

        finished_good = None
        if not finished_good_sku:
            errors.append("Finished good part number is required.")
        else:
            finished_good = Item.query.filter_by(sku=finished_good_sku).first()
            if finished_good is None:
                errors.append(
                    f"Finished good part number '{finished_good_sku}' was not found."
                )

        try:
            quantity = int(quantity_raw)
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Quantity must be a positive integer.")
            quantity = None

        promised_date = _parse_date(promised_date_raw, "Promised ship date", errors)
        scheduled_start_date = _parse_date(
            scheduled_start_raw, "Scheduled start date", errors
        )
        scheduled_completion_date = _parse_date(
            scheduled_completion_raw, "Scheduled completion date", errors
        )

        if (
            scheduled_start_date
            and scheduled_completion_date
            and scheduled_start_date > scheduled_completion_date
        ):
            errors.append("Scheduled start date must be on or before completion date.")

        if (
            promised_date
            and scheduled_completion_date
            and promised_date < scheduled_completion_date
        ):
            errors.append(
                "Promised ship date must be on or after the scheduled completion date."
            )

        try:
            bom_payload = json.loads(bom_raw)
            if not isinstance(bom_payload, list):
                raise ValueError
        except ValueError:
            bom_payload = []
            errors.append("Unable to read the BOM component details submitted.")

        try:
            routing_payload = json.loads(routing_raw)
            if not isinstance(routing_payload, list):
                raise ValueError
        except ValueError:
            routing_payload = []
            errors.append("Unable to read the routing information submitted.")

        form_data["bom"] = bom_payload
        form_data["steps"] = routing_payload

        bom_components = []
        component_lookup = {}
        component_skus_seen = set()
        if not bom_payload:
            errors.append("At least one BOM component is required.")
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

                component_entry = {
                    "sku": sku,
                    "item": component_item,
                    "quantity": component_quantity,
                }
                bom_components.append(component_entry)
                component_lookup[sku] = component_entry
                component_skus_seen.add(sku)

        routing_steps = []
        referenced_components = set()
        sequences_seen = set()
        if not routing_payload:
            errors.append("At least one routing step is required.")
        else:
            for entry in routing_payload:
                if not isinstance(entry, dict):
                    errors.append("Invalid routing step definition submitted.")
                    continue

                raw_sequence = entry.get("sequence")
                try:
                    sequence = int(raw_sequence)
                except (TypeError, ValueError):
                    errors.append("Routing step sequences must be whole numbers.")
                    continue
                if sequence in sequences_seen:
                    errors.append(
                        f"Routing step sequence {sequence} is defined more than once."
                    )
                    continue
                sequences_seen.add(sequence)

                work_cell = (entry.get("work_cell") or "").strip()
                instructions = (entry.get("instructions") or "").strip()
                if not instructions:
                    errors.append(
                        f"Routing step {sequence} must include work instructions."
                    )

                component_values = entry.get("components") or []
                if not isinstance(component_values, list):
                    errors.append(
                        f"Component usage for routing step {sequence} is not valid."
                    )
                    component_values = []

                resolved_components = []
                for sku in component_values:
                    if sku not in component_lookup:
                        errors.append(
                            f"Routing step {sequence} references unknown component {sku}."
                        )
                        continue
                    if sku in resolved_components:
                        continue
                    resolved_components.append(sku)
                    referenced_components.add(sku)

                routing_steps.append(
                    {
                        "sequence": sequence,
                        "work_cell": work_cell,
                        "instructions": instructions,
                        "components": resolved_components,
                    }
                )

        missing_component_usage = set(component_lookup) - referenced_components
        if missing_component_usage:
            missing_list = ", ".join(sorted(missing_component_usage))
            errors.append(
                "Each BOM component must be associated with at least one routing step. "
                f"Missing usage for: {missing_list}."
            )

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "orders/new.html", items=items, form_data=form_data
            )

        shortages = []
        reservations_needed = []
        if quantity is not None:
            for component_entry in bom_components:
                component_item = component_entry["item"]
                required_total = component_entry["quantity"] * quantity
                available_quantity = _available_quantity(component_item.id)
                component_entry["required_total"] = required_total
                component_entry["available_quantity"] = available_quantity
                if required_total > available_quantity:
                    shortages.append(
                        {
                            "item": component_item,
                            "required": required_total,
                            "available": available_quantity,
                        }
                    )
                else:
                    reservations_needed.append(
                        {
                            "item": component_item,
                            "quantity": required_total,
                        }
                    )

        today = datetime.utcnow().date()
        if scheduled_start_date and scheduled_start_date > today:
            order_status = OrderStatus.SCHEDULED
        else:
            order_status = OrderStatus.OPEN
        if shortages:
            order_status = OrderStatus.WAITING_MATERIAL

        existing_template = None
        if finished_good is not None:
            existing_template = BillOfMaterial.query.filter_by(
                item_id=finished_good.id
            ).first()

        order = Order(
            order_number=order_number,
            customer_name=customer_name,
            created_by=created_by,
            general_notes=general_notes_db_value,
            promised_date=promised_date,
            scheduled_start_date=scheduled_start_date,
            scheduled_completion_date=scheduled_completion_date,
            status=order_status,
        )
        order_line = OrderLine(
            order=order,
            item_id=finished_good.id,
            quantity=quantity,
            promised_date=promised_date,
            scheduled_start_date=scheduled_start_date,
            scheduled_completion_date=scheduled_completion_date,
        )
        db.session.add(order)
        db.session.add(order_line)

        bom_entities = {}
        for component_entry in bom_components:
            component_item = component_entry["item"]
            component_quantity = component_entry["quantity"]
            bom_component = OrderComponent(
                order_line=order_line,
                component_item_id=component_item.id,
                quantity=component_quantity,
            )
            db.session.add(bom_component)
            bom_entities[component_entry["sku"]] = bom_component

        if not shortages:
            for reservation_entry in reservations_needed:
                db.session.add(
                    Reservation(
                        order_line=order_line,
                        item_id=reservation_entry["item"].id,
                        quantity=reservation_entry["quantity"],
                    )
                )

        for step in sorted(routing_steps, key=lambda step: step["sequence"]):
            routing_step = RoutingStep(
                order=order,
                sequence=step["sequence"],
                work_cell=step["work_cell"] or None,
                description=step["instructions"],
            )
            db.session.add(routing_step)
            for component_sku in step["components"]:
                db.session.add(
                    RoutingStepComponent(
                        routing_step=routing_step,
                        order_component=bom_entities[component_sku],
                    )
                )

        bom_template_saved = False
        if save_bom_template and finished_good is not None:
            _, created_template, changed_template = _save_bom_template(
                finished_good, bom_components, replace_existing=False
            )
            bom_template_saved = created_template or changed_template

        db.session.commit()
        if shortages:
            shortage_summary = ", ".join(
                f"{entry['item'].sku} (required {_format_quantity(entry['required'])}, "
                f"available {_format_quantity(entry['available'])})"
                for entry in shortages
            )
            message = "Order created but waiting on material"
            if shortage_summary:
                message = f"{message}: {shortage_summary}"
            if bom_template_saved:
                message = f"{message} — BOM template saved for {finished_good.sku}."
            flash(message, "warning")
        else:
            success_message = "Order created and materials reserved"
            if bom_template_saved:
                success_message = (
                    f"{success_message}. BOM template saved for {finished_good.sku}."
                )
            elif save_bom_template and existing_template is not None:
                success_message = (
                    f"{success_message}. Existing BOM template for {finished_good.sku} "
                    "remains unchanged."
                )
            flash(success_message, "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template("orders/new.html", items=items, form_data=form_data)


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
        )
        .filter_by(id=order_id)
        .first_or_404()
    )
    context = _prepare_order_detail(order)
    return render_template("orders/view.html", **context)


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

    errors = []
    planned_consumptions = defaultdict(list)

    for step in order.routing_steps:
        desired_state = step.id in selected_ids
        if desired_state and not step.completed:
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

    if changes_made:
        db.session.commit()
        flash("Routing progress updated", "success")
    else:
        flash("No routing updates were made.", "info")

    return redirect(url_for("orders.view_order", order_id=order.id))


@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    status_choices = OrderStatus.ALL_STATUSES
    if request.method == "POST":
        status = request.form.get("status", order.status)
        general_notes = request.form.get("general_notes") or ""
        general_notes_db_value = general_notes if general_notes.strip() else None
        if status not in set(status_choices):
            flash("Invalid status", "danger")
            order.general_notes = general_notes_db_value
            return render_template(
                "orders/edit.html",
                order=order,
                statuses=status_choices,
                status_labels=OrderStatus.LABELS,
            )

        order.status = status
        order.general_notes = general_notes_db_value
        db.session.commit()
        flash("Order updated", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template(
        "orders/edit.html",
        order=order,
        statuses=status_choices,
        status_labels=OrderStatus.LABELS,
    )


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    flash("Order deleted", "success")
    return redirect(url_for("orders.orders_home"))
