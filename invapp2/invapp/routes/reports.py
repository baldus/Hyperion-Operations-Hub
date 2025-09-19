import csv
import io
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import Batch, Item, Location, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _parse_date_arg(raw_value):
    if not raw_value:
        return None
    return datetime.strptime(raw_value, "%Y-%m-%d").date()


def _collect_movement_rows(sku, location_code, start_date, end_date):
    query = (
        Movement.query.join(Item)
        .filter(Item.sku == sku)
        .options(joinedload(Movement.location))
    )
    if location_code:
        query = query.join(Location, Movement.location).filter(Location.code == location_code)
    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Movement.date >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, datetime.max.time())
        query = query.filter(Movement.date <= end_dt)

    movements = query.order_by(Movement.date.asc()).all()
    rows = []
    for movement in movements:
        quantity = movement.quantity
        if (movement.movement_type or "").upper() == "ISSUE":
            quantity = -quantity
        rows.append(
            {
                "date": movement.date.strftime("%Y-%m-%d"),
                "movement_type": movement.movement_type,
                "quantity": int(quantity or 0),
                "location": movement.location.code if movement.location else None,
            }
        )
    return rows


def _collect_stock_aging_rows(sku, location_code):
    batch_query = Batch.query.join(Item).filter(Item.sku == sku)
    batches = batch_query.all()
    rows = []
    today = datetime.utcnow().date()

    for batch in batches:
        received_date = batch.received_date.date() if batch.received_date else today
        quantity_query = Movement.query.filter(Movement.item_id == batch.item_id)
        if batch.id is None:
            quantity_query = quantity_query.filter(Movement.batch_id.is_(None))
        else:
            quantity_query = quantity_query.filter(Movement.batch_id == batch.id)
        if location_code:
            quantity_query = quantity_query.join(Location, Movement.location).filter(
                Location.code == location_code
            )
        total_quantity = quantity_query.with_entities(
            func.coalesce(func.sum(Movement.quantity), 0)
        ).scalar()

        rows.append(
            {
                "sku": batch.item.sku if batch.item else sku,
                "lot_number": batch.lot_number,
                "days": (today - received_date).days,
                "quantity": int(total_quantity or 0),
            }
        )

    return rows


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


@bp.route("/summary_data")
@login_required
def summary_data():
    sku = (request.args.get("sku") or "").strip()
    location_code = (request.args.get("location") or "").strip() or None

    if not sku:
        return jsonify({"error": "sku parameter is required"}), 400

    try:
        start_date = _parse_date_arg(request.args.get("start"))
        end_date = _parse_date_arg(request.args.get("end"))
    except ValueError:
        return jsonify({"error": "Dates must be in YYYY-MM-DD format"}), 400

    movement_rows = _collect_movement_rows(sku, location_code, start_date, end_date)
    stock_aging_rows = _collect_stock_aging_rows(sku, location_code)

    return jsonify({
        "movement_trends": movement_rows,
        "stock_aging": stock_aging_rows,
    })


@bp.route("/export")
@login_required
def export_report():
    sku = (request.args.get("sku") or "").strip()
    location_code = (request.args.get("location") or "").strip() or None

    if not sku:
        return jsonify({"error": "sku parameter is required"}), 400

    try:
        start_date = _parse_date_arg(request.args.get("start"))
        end_date = _parse_date_arg(request.args.get("end"))
    except ValueError:
        return jsonify({"error": "Dates must be in YYYY-MM-DD format"}), 400

    movement_rows = _collect_movement_rows(sku, location_code, start_date, end_date)
    stock_aging_rows = _collect_stock_aging_rows(sku, location_code)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        stock_output = io.StringIO()
        stock_writer = csv.writer(stock_output)
        stock_writer.writerow(["sku", "lot_number", "days", "quantity"])
        for row in stock_aging_rows:
            stock_writer.writerow(
                [row.get("sku"), row.get("lot_number"), row.get("days"), row.get("quantity")]
            )
        archive.writestr("stock_aging.csv", stock_output.getvalue())

        movement_output = io.StringIO()
        movement_writer = csv.writer(movement_output)
        movement_writer.writerow(["date", "movement_type", "quantity", "location"])
        for row in movement_rows:
            movement_writer.writerow(
                [
                    row.get("date"),
                    row.get("movement_type"),
                    row.get("quantity"),
                    row.get("location"),
                ]
            )
        archive.writestr("movement_trends.csv", movement_output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=inventory_report.zip"},
    )


@bp.route("/")
def reports_home():
    now = datetime.utcnow()
    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)

    usage_query = (
        db.session.query(
            Item.sku,
            Item.name,
            func.coalesce(-func.sum(Movement.quantity), 0).label("total_usage"),
            func.coalesce(
                func.sum(
                    case(
                        (Movement.date >= cutoff_30, -Movement.quantity),
                        else_=0,
                    )
                ),
                0,
            ).label("usage_30"),
            func.coalesce(
                func.sum(
                    case(
                        (Movement.date >= cutoff_90, -Movement.quantity),
                        else_=0,
                    )
                ),
                0,
            ).label("usage_90"),
        )
        .join(Item, Movement.item_id == Item.id)
        .filter(
            Movement.movement_type == "ISSUE",
            Movement.quantity < 0,
        )
        .group_by(Item.id, Item.sku, Item.name)
        .having(func.sum(Movement.quantity) < 0)
        .order_by(
            func.sum(
                case((Movement.date >= cutoff_30, -Movement.quantity), else_=0)
            ).desc()
        )
        .limit(100)
        .all()
    )

    usage_rows = [
        {
            "sku": sku,
            "name": name,
            "usage_30": int(usage_30 or 0),
            "usage_90": int(usage_90 or 0),
            "usage_total": int(total_usage or 0),
        }
        for sku, name, total_usage, usage_30, usage_90 in usage_query
    ]

    usage_window_30 = cutoff_30.strftime("%Y-%m-%d")
    usage_window_90 = cutoff_90.strftime("%Y-%m-%d")

    return render_template(
        "reports/home.html",
        usage_rows=usage_rows,
        usage_window_30=usage_window_30,
        usage_window_90=usage_window_90,
    )

@bp.route("/generate")
def generate_reports():
    # In-memory zip file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Items
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
        for i in Item.query.all():
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
                po_number or "-"
            ])
        zf.writestr("movements.csv", output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports.zip"}
    )
