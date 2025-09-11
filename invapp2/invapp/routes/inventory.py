import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from invapp.models import db, Item, Location, Batch, Movement, Reservation
from datetime import datetime

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

############################
# HOME
############################
@bp.route("/")
def inventory_home():
    return render_template("inventory/home.html")

############################
# CYCLE COUNT ROUTES
############################
@bp.route("/cycle-count", methods=["GET", "POST"])
def cycle_count_home():
    items = Item.query.all()
    locations = Location.query.all()
    batches = Batch.query.all()

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

    # Recent cycle counts with related data eagerly loaded to avoid extra queries
    records = (
        Movement.query
        .filter(Movement.movement_type.in_(["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]))
        .order_by(Movement.date.desc())
        .limit(50)
        .options(
            joinedload(Movement.item),
            joinedload(Movement.location),
            joinedload(Movement.batch),
        )
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
        Movement.query
        .filter(Movement.movement_type.in_(["CYCLE_COUNT_CONFIRM", "CYCLE_COUNT_ADJUSTMENT"]))
        .order_by(Movement.date.desc())
        .options(
            joinedload(Movement.item),
            joinedload(Movement.location),
            joinedload(Movement.batch),
        )
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
            rec.movement_type
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=cycle_counts.csv"
    return response



############################
# ITEM ROUTES
############################
@bp.route("/items")
def list_items():
    items = Item.query.all()
    return render_template("inventory/list_items.html", items=items)


@bp.route("/item/add", methods=["GET", "POST"])
def add_item():
    if request.method == "POST":
        max_sku = db.session.query(db.func.max(Item.sku.cast(db.Integer))).scalar()
        next_sku = str(int(max_sku) + 1) if max_sku else "1"

        item = Item(
            sku=next_sku,
            name=request.form["name"],
            unit=request.form.get("unit", "ea"),
            description=request.form.get("description", ""),
            min_stock=request.form.get("min_stock", 0)
        )
        db.session.add(item)
        db.session.commit()
        flash(f"Item added successfully with SKU {next_sku}", "success")
        return redirect(url_for("inventory.list_items"))

    max_sku = db.session.query(db.func.max(Item.sku.cast(db.Integer))).scalar()
    next_sku = str(int(max_sku) + 1) if max_sku else "1"
    return render_template("inventory/add_item.html", next_sku=next_sku)


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

        count_new, count_updated = 0, 0
        for row in csv_input:
            sku = row.get("sku", "").strip()
            name = row.get("name", "").strip()
            unit = row.get("unit", "ea").strip()
            description = row.get("description", "").strip()
            min_stock = int(row.get("min_stock", 0))

            existing = Item.query.filter_by(sku=sku).first() if sku else None
            if existing:
                existing.name = name or existing.name
                existing.unit = unit or existing.unit
                existing.description = description or existing.description
                existing.min_stock = min_stock or existing.min_stock
                count_updated += 1
            else:
                if not sku:
                    max_sku = db.session.query(db.func.max(Item.sku.cast(db.Integer))).scalar()
                    sku = str(int(max_sku) + 1) if max_sku else "1"
                item = Item(sku=sku, name=name, unit=unit, description=description, min_stock=min_stock)
                db.session.add(item)
                count_new += 1

        db.session.commit()
        flash(f"Items imported: {count_new} new, {count_updated} updated", "success")
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
    writer.writerow(["sku", "name", "unit", "description", "min_stock"])
    for i in items:
        writer.writerow([i.sku, i.name, i.unit, i.description, i.min_stock])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=items.csv"
    return response


############################
# LOCATION ROUTES
############################
@bp.route("/locations")
def list_locations():
    locations = Location.query.all()
    return render_template("inventory/list_locations.html", locations=locations)


@bp.route("/location/add", methods=["GET", "POST"])
def add_location():
    if request.method == "POST":
        loc = Location(code=request.form["code"], description=request.form.get("description", ""))
        db.session.add(loc)
        db.session.commit()
        flash("Location added successfully", "success")
        return redirect(url_for("inventory.list_locations"))
    return render_template("inventory/add_location.html")


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

    totals_on_hand = dict(
        db.session.query(
            Movement.item_id,
            func.sum(Movement.quantity).label("total_on_hand")
        )
        .group_by(Movement.item_id)
        .all()
    )

    reserved_rows = (
        db.session.query(
            Reservation.item_id,
            Reservation.batch_id,
            Reservation.location_id,
            func.sum(Reservation.quantity).label("reserved")
        )
        .filter_by(consumed=False)
        .group_by(Reservation.item_id, Reservation.batch_id, Reservation.location_id)
        .all()
    )

    reserved_totals = dict(
        db.session.query(
            Reservation.item_id,
            func.sum(Reservation.quantity).label("total_reserved")
        )
        .filter_by(consumed=False)
        .group_by(Reservation.item_id)
        .all()
    )

    reserved_map = {
        (r.item_id, r.batch_id, r.location_id): r.reserved for r in reserved_rows
    }

    balances = []
    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    for item_id, batch_id, location_id, on_hand in rows:
        key = (item_id, batch_id, location_id)
        reserved = reserved_map.get(key, 0)
        balances.append({
            "item": items.get(item_id),
            "batch": batches.get(batch_id) if batch_id else None,
            "location": locations.get(location_id),
            "on_hand": int(on_hand),
            "reserved": int(reserved),
            "available": int(on_hand - reserved),
            "total_on_hand": int(totals_on_hand.get(item_id, 0)),
            "total_reserved": int(reserved_totals.get(item_id, 0)),
            "total_available": int(
                totals_on_hand.get(item_id, 0) - reserved_totals.get(item_id, 0)
            ),
        })

    return render_template(
        "inventory/list_stock.html",
        balances=balances,
        totals=totals_on_hand,
        totals_reserved=reserved_totals,
        items=items,
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

        count_new, count_updated = 0, 0
        for row in csv_input:
            sku = row["sku"].strip()
            loc_code = row["location_code"].strip()
            qty = int(row.get("quantity", 0))
            lot_number = (row.get("lot_number") or "").strip() or None
            person = (row.get("person") or "").strip() or None
            reference = (row.get("reference") or "Bulk Adjust").strip()

            item = item_map.get(sku)
            location = loc_map.get(loc_code)
            if not item or not location:
                continue

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

    reserved_rows = (
        db.session.query(
            Reservation.item_id,
            Reservation.batch_id,
            Reservation.location_id,
            func.sum(Reservation.quantity).label("reserved")
        )
        .filter_by(consumed=False)
        .group_by(Reservation.item_id, Reservation.batch_id, Reservation.location_id)
        .all()
    )
    reserved_map = {
        (r.item_id, r.batch_id, r.location_id): r.reserved for r in reserved_rows
    }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "sku",
        "item_name",
        "location",
        "lot_number",
        "on_hand",
        "reserved",
        "available",
    ])

    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    for item_id, batch_id, location_id, on_hand in rows:
        sku = items[item_id].sku if item_id in items else "UNKNOWN"
        name = items[item_id].name if item_id in items else "UNKNOWN"
        loc = locations[location_id].code if location_id in locations else "UNKNOWN"
        lot = batches[batch_id].lot_number if batch_id and batch_id in batches else "-"
        reserved = reserved_map.get((item_id, batch_id, location_id), 0)
        available = on_hand - reserved
        writer.writerow([sku, name, loc, lot, on_hand, reserved, available])

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

        flash(f"Receiving recorded! Lot: {lot_number}", "success")
        return redirect(url_for("inventory.receiving"))

    # Display recent receipts
    records = (
        Movement.query
        .filter_by(movement_type="RECEIPT")
        .order_by(Movement.date.desc())
        .limit(50)
        .all()
    )
    item_map = {i.id: i for i in Item.query.all()}
    loc_map = {l.id: l for l in Location.query.all()}
    batch_map = {b.id: b for b in Batch.query.all()}
    return render_template(
        "inventory/receiving.html",
        records=records,
        locations=locations,
        item_map=item_map,
        loc_map=loc_map,
        batch_map=batch_map
    )

############################
# MOVE / TRANSFER ROUTES
############################
@bp.route("/move", methods=["GET", "POST"])
def move_home():
    items = Item.query.all()
    locations = Location.query.all()
    batches = Batch.query.all()

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

    return render_template(
        "inventory/move.html",
        items=items,
        locations=locations,
        batches=batches,
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


@bp.route("/history/export")
def export_history():
    """
    Export all transactions (Movement table) to CSV.
    """
    records = Movement.query.order_by(Movement.date.desc()).all()
    items = {i.id: i for i in Item.query.all()}
    locations = {l.id: l for l in Location.query.all()}
    batches = {b.id: b for b in Batch.query.all()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date", "sku", "item_name", "movement_type", "quantity",
        "location", "lot_number", "person", "reference", "po_number"
    ])

    for mv in records:
        sku = items[mv.item_id].sku if mv.item_id in items else "???"
        name = items[mv.item_id].name if mv.item_id in items else "Unknown Item"
        loc = locations[mv.location_id].code if mv.location_id in locations else "-"
        lot = batches[mv.batch_id].lot_number if mv.batch_id and mv.batch_id in batches else "-"
        writer.writerow([
            mv.date.strftime("%Y-%m-%d %H:%M"),
            sku,
            name,
            mv.movement_type,
            mv.quantity,
            loc,
            lot,
            mv.person or "-",
            mv.reference or "-",
            mv.po_number or "-"
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=transaction_history.csv"
    return response
