import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, load_only

from invapp.models import (
    Batch,
    Item,
    Location,
    Movement,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    Reservation,
    db,
)

bp = Blueprint("inventory", __name__, url_prefix="/inventory")


UNASSIGNED_LOCATION_CODE = "UNASSIGNED"


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


def _get_highest_numeric_sku():
    """Return the highest numeric SKU value as an integer."""
    sku_rows = db.session.query(Item.sku).all()
    numeric_values = []
    for (sku,) in sku_rows:
        if sku is None:
            continue
        sku_str = str(sku).strip()
        if sku_str.isdigit():
            numeric_values.append(int(sku_str))
    return max(numeric_values) if numeric_values else None

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
    on_hand_map = {item_id: int(total or 0) for item_id, total in movement_totals}

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
    reserved_map = {item_id: int(total or 0) for item_id, total in reservation_totals}

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
        min_stock = int(item.min_stock or 0)
        if min_stock <= 0:
            continue
        total_on_hand = on_hand_map.get(item.id, 0)
        coverage = total_on_hand / float(min_stock) if min_stock else None
        entry = {
            "item": item,
            "on_hand": total_on_hand,
            "min_stock": min_stock,
            "coverage": coverage,
        }
        if total_on_hand < (min_stock * 1.05):
            low_stock_items.append(entry)
        elif total_on_hand < (min_stock * 1.25):
            near_stock_items.append(entry)

    def _coverage_sort_key(entry):
        coverage = entry.get("coverage")
        return coverage if coverage is not None else float("inf")

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

    required_by_item = defaultdict(int)
    order_refs = defaultdict(list)
    for order in waiting_orders:
        for line in order.order_lines:
            line_quantity = int(line.quantity or 0)
            if line_quantity <= 0:
                continue
            for component in line.components:
                component_quantity = int(component.quantity or 0)
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
        available = on_hand_map.get(item_id, 0) - reserved_map.get(item_id, 0)
        if available < 0:
            available = 0
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
            value = int(raw_value)
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


@bp.route("/scan")
def scan_inventory():
    lookup_template = url_for("inventory.lookup_item_api", sku="__SKU__")
    return render_template(
        "inventory/scan.html",
        lookup_template=lookup_template,
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
            )
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
    search = request.args.get("search", "")

    query = Item.query
    if selected_type:
        query = query.filter(Item.type == selected_type)
    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(Item.sku.ilike(like_pattern), Item.name.ilike(like_pattern))
        )

    sort_columns = {
        "sku": Item.sku.asc(),
        "name": Item.name.asc(),
        "type": Item.type.asc(),
        "unit": Item.unit.asc(),
        "min_stock": Item.min_stock.asc(),
        "list_price": Item.list_price.asc(),
        "last_unit_cost": Item.last_unit_cost.asc(),
        "item_class": Item.item_class.asc(),
    }
    query = query.order_by(sort_columns.get(sort_param, Item.sku.asc()))

    pagination = query.paginate(page=page, per_page=size, error_out=False)

    types_query = (
        db.session.query(Item.type)
        .filter(Item.type.isnot(None))
        .filter(Item.type != "")
        .distinct()
        .order_by(Item.type.asc())
    )
    available_types = [row[0] for row in types_query]
    return render_template(
        "inventory/list_items.html",
        items=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
        available_types=available_types,
        selected_type=selected_type,
        sort=sort_param,
        search=search,
    )


@bp.route("/item/add", methods=["GET", "POST"])
def add_item():
    if request.method == "POST":
        max_sku = _get_highest_numeric_sku()
        next_sku = str(max_sku + 1) if max_sku is not None else "1"

        min_stock_raw = request.form.get("min_stock", 0)
        try:
            min_stock = int(min_stock_raw or 0)
        except (TypeError, ValueError):
            min_stock = 0

        notes_raw = request.form.get("notes")
        notes = notes_raw.strip() if notes_raw is not None else None
        notes_value = notes or None

        item = Item(
            sku=next_sku,
            name=request.form["name"],
            type=request.form.get("type", "").strip() or None,
            unit=request.form.get("unit", "ea").strip() or "ea",
            description=request.form.get("description", ""),
            min_stock=min_stock,
            notes=notes_value,
            list_price=_parse_decimal(request.form.get("list_price")),
            last_unit_cost=_parse_decimal(request.form.get("last_unit_cost")),
            item_class=request.form.get("item_class", "").strip() or None,
        )
        db.session.add(item)
        db.session.commit()
        note_msg = " (notes saved)" if notes_value else ""
        flash(f"Item added successfully with SKU {next_sku}{note_msg}", "success")
        return redirect(url_for("inventory.list_items"))

    max_sku = _get_highest_numeric_sku()
    next_sku = str(max_sku + 1) if max_sku is not None else "1"
    return render_template("inventory/add_item.html", next_sku=next_sku)


@bp.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
def edit_item(item_id):
    if not session.get("is_admin"):
        next_target = (
            request.full_path if request.method == "GET" and request.query_string else request.path
        )
        flash("Administrator access is required to edit items.", "danger")
        return redirect(url_for("admin.login", next=next_target))

    item = Item.query.get_or_404(item_id)

    if request.method == "POST":
        item.name = request.form["name"]
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

        db.session.commit()
        if notes_raw is not None:
            if notes_value:
                note_msg = " (notes saved)"
            else:
                note_msg = " (notes cleared)"
        else:
            note_msg = ""
        flash(f"Item {item.sku} updated successfully{note_msg}", "success")
        return redirect(url_for("inventory.list_items"))

    return render_template("inventory/edit_item.html", item=item)


@bp.route("/item/<int:item_id>/delete", methods=["POST"])
def delete_item(item_id):
    if not session.get("is_admin"):
        flash("Administrator access is required to delete items.", "danger")
        next_target = url_for("inventory.edit_item", item_id=item_id)
        return redirect(url_for("admin.login", next=next_target))

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


@bp.route("/items/import", methods=["GET", "POST"])
def import_items():
    """
    Import items from CSV.
    - If sku exists → update the record.
    - If sku missing → auto-generate next sequential sku.
    - 'id' column (from export) is ignored if present.
    """
    if request.method == "POST":
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"))
        csv_input = csv.DictReader(stream)

        max_sku_val = _get_highest_numeric_sku()
        next_sku = (max_sku_val + 1) if max_sku_val is not None else 1

        count_new, count_updated = 0, 0
        for row in csv_input:
            sku = row.get("sku", "").strip()
            name = row.get("name", "").strip()
            unit = row.get("unit", "ea").strip()
            description = row.get("description", "").strip()
            min_stock_raw = row.get("min_stock", 0)
            try:
                min_stock = int(min_stock_raw or 0)
            except (TypeError, ValueError):
                min_stock = 0

            has_type_column = "type" in row
            item_type = (row.get("type") or "").strip() if has_type_column else None
            has_notes_column = "notes" in row
            if has_notes_column:
                notes_raw = row.get("notes")
                notes_clean = notes_raw.strip() if notes_raw is not None else ""
                notes_value = notes_clean or None
            else:
                notes_value = None

            has_list_price_column = "list_price" in row
            has_last_unit_cost_column = "last_unit_cost" in row
            has_item_class_column = "item_class" in row

            list_price_value = (
                _parse_decimal(row.get("list_price")) if has_list_price_column else None
            )
            last_unit_cost_value = (
                _parse_decimal(row.get("last_unit_cost"))
                if has_last_unit_cost_column
                else None
            )
            item_class_value = (
                (row.get("item_class") or "").strip() if has_item_class_column else None
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
        flash(
            (
                "Items imported: "
                f"{count_new} new, {count_updated} updated "
                "(extended fields processed)"
            ),
            "success",
        )
        return redirect(url_for("inventory.list_items"))

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
    pagination = Location.query.paginate(page=page, per_page=size, error_out=False)
    return render_template(
        "inventory/list_locations.html",
        locations=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
    )


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
def edit_location(location_id):
    if not session.get("is_admin"):
        next_target = (
            request.full_path if request.method == "GET" and request.query_string else request.path
        )
        flash("Administrator access is required to edit locations.", "danger")
        return redirect(url_for("admin.login", next=next_target))

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


@bp.route("/location/<int:location_id>/delete", methods=["POST"])
def delete_location(location_id):
    if not session.get("is_admin"):
        flash("Administrator access is required to delete locations.", "danger")
        next_target = url_for("inventory.edit_location", location_id=location_id)
        return redirect(url_for("admin.login", next=next_target))

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
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"))
        csv_input = csv.DictReader(stream)

        count_new, count_updated = 0, 0
        for row in csv_input:
            code = row["code"].strip()
            desc = row.get("description", "").strip()

            existing = Location.query.filter_by(code=code).first()
            if existing:
                existing.description = desc or existing.description
                count_updated += 1
            else:
                loc = Location(code=code, description=desc)
                db.session.add(loc)
                count_new += 1

        db.session.commit()
        flash(f"Locations imported: {count_new} new, {count_updated} updated", "success")
        return redirect(url_for("inventory.list_locations"))

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
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"))
        csv_input = csv.DictReader(stream)

        item_map = {i.sku: i for i in Item.query.all()}
        loc_map = {l.code: l for l in Location.query.all()}

        placeholder_location = loc_map.get(UNASSIGNED_LOCATION_CODE)
        if not placeholder_location:
            placeholder_location = Location(
                code=UNASSIGNED_LOCATION_CODE,
                description="Unassigned staging location",
            )
            db.session.add(placeholder_location)
            db.session.flush()
            loc_map[UNASSIGNED_LOCATION_CODE] = placeholder_location

        count_new, count_updated = 0, 0
        for row in csv_input:
            sku = row["sku"].strip()
            loc_code = (row.get("location_code") or "").strip()
            qty = int(row.get("quantity", 0))
            lot_number = (row.get("lot_number") or "").strip() or None
            person = (row.get("person") or "").strip() or None
            reference = (row.get("reference") or "Bulk Adjust").strip()

            item = item_map.get(sku)
            if not item:
                continue

            location = loc_map.get(loc_code) if loc_code else None
            if not location:
                location = placeholder_location

            batch = None
            if lot_number:
                batch = Batch.query.filter_by(item_id=item.id, lot_number=lot_number).first()
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
                reference=reference
            )
            db.session.add(mv)

        db.session.commit()
        flash(f"Stock adjustments processed: {count_new} new batches, {count_updated} updated batches", "success")
        return redirect(url_for("inventory.list_stock"))

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

    if request.method == "POST":
        sku = request.form["sku"].strip()
        qty = int(request.form["qty"])
        person = request.form["person"].strip()
        po_number = request.form.get("po_number", "").strip() or None
        location_id = int(request.form["location_id"])

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

        # Record movement
        mv = Movement(
            item_id=item.id,
            batch_id=batch_id,
            location_id=location_id,
            quantity=qty,
            movement_type="RECEIPT",
            person=person,
            po_number=po_number,
            reference="PO Receipt" if po_number else "Receipt"
        )
        db.session.add(mv)
        db.session.commit()
        try:
            from invapp.printing.zebra import print_receiving_label

            if not print_receiving_label(item.sku, item.name, qty):
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

    return render_template(
        "inventory/receiving.html",
        records=records,
        locations=locations,
    )


@bp.post("/receiving/<int:receipt_id>/reprint")
def reprint_receiving_label(receipt_id: int):
    """Reprint a previously generated receiving label."""
    rec = (
        Movement.query.options(joinedload(Movement.item).load_only(Item.sku, Item.name))
        .filter_by(id=receipt_id, movement_type="RECEIPT")
        .first_or_404()
    )

    item = rec.item
    qty = rec.quantity

    try:
        from invapp.printing.zebra import print_receiving_label

        if not print_receiving_label(item.sku, item.name, qty):
            flash("Failed to print receiving label.", "warning")
        else:
            flash("Label reprinted.", "success")
    except Exception:
        flash("Failed to print receiving label.", "warning")

    return redirect(url_for("inventory.receiving"))

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
