import csv
import io
import math
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from sqlalchemy import MetaData, func, inspect, or_
from sqlalchemy.orm import load_only, joinedload
from invapp.models import db, Item, Location, Batch, Movement
from datetime import datetime

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

############################
# HOME
############################
@bp.route("/")
def inventory_home():
    items = Item.query.options(
        load_only(Item.id, Item.sku, Item.name, Item.min_stock)
    ).all()
    item_map = {item.id: item for item in items}
    stock_totals = _calculate_stock_totals()
    stock_alerts = _build_stock_alerts(item_map, stock_totals)
    pending_order_items = _fetch_pending_order_items(item_map, stock_totals)

    return render_template(
        "inventory/home.html",
        pending_order_items=pending_order_items,
        critical_stock=stock_alerts["critical"],
        warning_stock=stock_alerts["warning"],
    )


def _calculate_stock_totals():
    """Return current on-hand totals for each item id."""
    totals_query = (
        db.session.query(Movement.item_id, func.sum(Movement.quantity))
        .group_by(Movement.item_id)
        .all()
    )
    return {item_id: int(total or 0) for item_id, total in totals_query}


def _build_stock_alerts(item_map, stock_totals):
    """Group items that are at or approaching their minimum stock levels."""
    critical = []
    warning = []
    for item in item_map.values():
        min_stock = item.min_stock or 0
        if min_stock <= 0:
            continue

        on_hand = stock_totals.get(item.id, 0)
        if on_hand < min_stock:
            critical.append({"item": item, "on_hand": on_hand, "minimum": min_stock})
        else:
            warning_threshold = math.ceil(min_stock * 1.25)
            if on_hand < warning_threshold:
                warning.append(
                    {
                        "item": item,
                        "on_hand": on_hand,
                        "minimum": min_stock,
                        "threshold": warning_threshold,
                    }
                )

    critical.sort(key=lambda entry: entry["item"].sku)
    warning.sort(key=lambda entry: entry["item"].sku)
    return {"critical": critical, "warning": warning}


def _fetch_pending_order_items(item_map, stock_totals):
    """Return outstanding order line items grouped by inventory item."""
    try:
        inspector = inspect(db.engine)
    except Exception:
        return []

    table_names = set(inspector.get_table_names())
    required_tables = {"order", "order_item", "item"}
    if not required_tables.issubset(table_names):
        return []

    metadata = MetaData()
    try:
        metadata.reflect(db.engine, only=required_tables)
    except Exception:
        return []

    orders_table = metadata.tables.get("order")
    order_items_table = metadata.tables.get("order_item")
    items_table = metadata.tables.get("item")
    if not orders_table or not order_items_table or not items_table:
        return []

    item_id_column = order_items_table.c.get("item_id")
    quantity_column = order_items_table.c.get("quantity")
    if item_id_column is None or quantity_column is None:
        return []

    columns = [
        item_id_column.label("item_id"),
        quantity_column.label("quantity"),
    ]

    # Optional columns
    for col_name in ("sku", "name"):
        col = items_table.c.get(col_name)
        if col is not None:
            columns.append(col.label(col_name))

    for col_name in ("order_number", "status"):
        col = orders_table.c.get(col_name)
        if col is not None:
            columns.append(col.label(col_name))

    query = (
        db.session.query(*columns)
        .select_from(order_items_table)
        .join(orders_table, order_items_table.c.order_id == orders_table.c.id)
        .join(items_table, order_items_table.c.item_id == items_table.c.id)
    )

    status_column = orders_table.c.get("status")
    if status_column is not None:
        query = query.filter(
            ~status_column.in_(
                ["completed", "complete", "closed", "cancelled", "canceled", "done"]
            )
        )

    try:
        rows = query.all()
    except Exception:
        return []

    pending = {}
    for row in rows:
        data = row._mapping
        item_id = data.get("item_id")
        quantity = data.get("quantity") or 0
        if not item_id or quantity <= 0:
            continue

        item = item_map.get(item_id)
        if not item:
            continue

        entry = pending.setdefault(
            item_id,
            {"item": item, "quantity": 0, "orders": set(), "statuses": set()},
        )
        entry["quantity"] += int(quantity)

        if order_number := data.get("order_number"):
            entry["orders"].add(order_number)
        if status_value := data.get("status"):
            entry["statuses"].add(status_value)

    results = []
    for item_id, entry in pending.items():
        on_hand = stock_totals.get(item_id, 0)
        quantity = entry["quantity"]
        results.append(
            {
                "item": entry["item"],
                "quantity": quantity,
                "orders": sorted(entry["orders"]),
                "statuses": sorted(entry["statuses"]),
                "on_hand": on_hand,
                "minimum": entry["item"].min_stock or 0,
                "shortfall": max(quantity - on_hand, 0),
            }
        )

    results.sort(key=lambda entry: entry["item"].sku)
    return results


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
            movement_type, qty_to_record = "CYCLE_COUNT_CONFIRM", 0
        else:
            movement_type, qty_to_record = "CYCLE_COUNT_ADJUSTMENT", diff

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
        .filter(Movement.movement_type.in_(["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]))
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
        .filter(Movement.movement_type.in_(["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]))
        .order_by(Movement.date.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["date", "sku", "item", "lot_number", "location", "book_vs_counted", "person", "movement_type"]
    )

    for rec in records:
        writer.writerow([
            rec.date.strftime("%Y-%m-%d %H:%M"),
            rec.item.sku if rec.item else "???",
            rec.item.name if rec.item else "Unknown",
            rec.batch.lot_number if rec.batch else "-",
            rec.location.code if rec.location else "-",
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
# (unchanged, but cleaned up formatting where needed)
# ...


############################
# STOCK ROUTES
############################
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
        lot_number = request.form.get("lot_number", "").strip() or None

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
            reference=reference,
        )
        db.session.add(mv)
        db.session.commit()

        flash(f"Adjustment saved for SKU {sku}", "success")
        return redirect(url_for("inventory.list_stock"))

    return render_template("inventory/adjust_stock.html", items=items, locations=locations)
