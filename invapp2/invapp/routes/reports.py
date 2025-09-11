import io, csv, zipfile
from datetime import datetime

from flask import Blueprint, Response, render_template, request, jsonify
from flask_login import login_required
from sqlalchemy import func

from invapp.models import db, Item, Location, Batch, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.before_request
@login_required
def require_login():
    pass

@bp.route("/")
def reports_home():
    return render_template("reports/home.html")


def _apply_filters(query, sku=None, location=None, start=None, end=None):
    if sku:
        query = query.join(Item).filter(Item.sku == sku)
    if location:
        query = query.join(Location).filter(Location.code == location)
    if start:
        query = query.filter(Movement.date >= start)
    if end:
        query = query.filter(Movement.date <= end)
    return query


@bp.route("/summary_data")
def summary_data():
    sku = request.args.get("sku")
    location = request.args.get("location")
    start = request.args.get("start")
    end = request.args.get("end")
    start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") if end else None

    movement_q = _apply_filters(Movement.query, sku, location, start_dt, end_dt)
    movement_data = (
        movement_q.with_entities(func.date(Movement.date).label("day"), func.sum(Movement.quantity))
        .group_by("day")
        .order_by("day")
        .all()
    )
    trends = [{"date": str(day), "quantity": qty} for day, qty in movement_data]

    batch_q = Batch.query.join(Item)
    if sku:
        batch_q = batch_q.filter(Item.sku == sku)
    today = datetime.utcnow().date()
    aging = [
        {
            "sku": b.item.sku,
            "lot_number": b.lot_number or "-",
            "days": (today - b.received_date.date()).days,
            "quantity": b.quantity,
        }
        for b in batch_q.all()
    ]

    return jsonify({"movement_trends": trends, "stock_aging": aging})

@bp.route("/generate")
def generate_reports():
    # In-memory zip file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Items
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sku", "name", "unit", "description", "min_stock"])
        for i in Item.query.all():
            writer.writerow([i.sku, i.name, i.unit, i.description, i.min_stock])
        zf.writestr("items.csv", output.getvalue())

        # Locations
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["code", "description"])
        for l in Location.query.all():
            writer.writerow([l.code, l.description])
        zf.writestr("locations.csv", output.getvalue())

        # Batches
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "item_sku", "lot_number", "quantity"])
        for b in Batch.query.all():
            writer.writerow([b.id, b.item.sku if b.item else "?", b.lot_number, b.quantity])
        zf.writestr("batches.csv", output.getvalue())

        # Movements
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "date", "sku", "item_name", "movement_type", "quantity",
            "location", "lot_number", "person", "reference", "po_number"
        ])
        for mv in Movement.query.all():
            writer.writerow([
                mv.date.strftime("%Y-%m-%d %H:%M"),
                mv.item.sku if mv.item else "???",
                mv.item.name if mv.item else "Unknown",
                mv.movement_type,
                mv.quantity,
                mv.location.code if mv.location else "-",
                mv.batch.lot_number if mv.batch else "-",
                mv.person or "-",
                mv.reference or "-",
                mv.po_number or "-"
            ])
        zf.writestr("movements.csv", output.getvalue())

        # Stock aging summary
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sku", "lot_number", "days_in_stock", "quantity"])
        today = datetime.utcnow().date()
        for b in Batch.query.join(Item).all():
            writer.writerow([
                b.item.sku,
                b.lot_number or "-",
                (today - b.received_date.date()).days,
                b.quantity,
            ])
        zf.writestr("stock_aging.csv", output.getvalue())

        # Movement trends summary
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "quantity"])
        movement_data = (
            db.session.query(func.date(Movement.date).label("day"), func.sum(Movement.quantity))
            .group_by("day")
            .order_by("day")
            .all()
        )
        for day, qty in movement_data:
            writer.writerow([day, qty])
        zf.writestr("movement_trends.csv", output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports.zip"}
    )


@bp.route("/export")
def export_filtered():
    sku = request.args.get("sku")
    location = request.args.get("location")
    start = request.args.get("start")
    end = request.args.get("end")
    start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") if end else None

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Stock aging
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sku", "lot_number", "days_in_stock", "quantity"])
        today = datetime.utcnow().date()
        batch_q = Batch.query.join(Item)
        if sku:
            batch_q = batch_q.filter(Item.sku == sku)
        for b in batch_q.all():
            writer.writerow([
                b.item.sku,
                b.lot_number or "-",
                (today - b.received_date.date()).days,
                b.quantity,
            ])
        zf.writestr("stock_aging.csv", output.getvalue())

        # Movement trends
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "quantity"])
        movement_q = _apply_filters(Movement.query, sku, location, start_dt, end_dt)
        movement_data = (
            movement_q.with_entities(func.date(Movement.date).label("day"), func.sum(Movement.quantity))
            .group_by("day")
            .order_by("day")
            .all()
        )
        for day, qty in movement_data:
            writer.writerow([day, qty])
        zf.writestr("movement_trends.csv", output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=custom_reports.zip"},
    )
