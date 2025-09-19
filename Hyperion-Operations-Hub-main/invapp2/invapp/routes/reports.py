import io, csv, zipfile
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import case, func

from invapp.models import db, Item, Location, Batch, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"

@bp.route("/")
@login_required
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
@login_required
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


def _parse_date_param(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _collect_summary_data(sku=None, location_code=None, start_date=None, end_date=None):
    movement_query = (
        Movement.query.join(Item).join(Location)
    )
    if sku:
        movement_query = movement_query.filter(Item.sku == sku)
    if location_code:
        movement_query = movement_query.filter(Location.code == location_code)
    if start_date:
        movement_query = movement_query.filter(
            Movement.date >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        movement_query = movement_query.filter(
            Movement.date <= datetime.combine(end_date, datetime.max.time())
        )

    movements = [
        {
            "date": movement.date.strftime("%Y-%m-%d"),
            "movement_type": movement.movement_type,
            "quantity": int(movement.quantity),
            "location": movement.location.code if movement.location else None,
        }
        for movement in movement_query.order_by(Movement.date.asc()).all()
    ]

    batch_query = Batch.query.join(Item)
    if sku:
        batch_query = batch_query.filter(Item.sku == sku)
    today = datetime.utcnow().date()
    aging = []
    for batch in batch_query.all():
        received = batch.received_date.date() if batch.received_date else today
        days_old = max(0, (today - received).days)
        aging.append(
            {
                "sku": batch.item.sku if batch.item else None,
                "lot_number": batch.lot_number or "",
                "quantity": int(batch.quantity or 0),
                "days": days_old,
            }
        )

    return movements, aging


@bp.route("/summary_data")
@login_required
def summary_data():
    sku = request.args.get("sku", "").strip() or None
    location_code = request.args.get("location", "").strip() or None
    start_raw = request.args.get("start")
    end_raw = request.args.get("end")

    start_date = _parse_date_param(start_raw)
    end_date = _parse_date_param(end_raw)
    if (start_raw and start_date is None) or (end_raw and end_date is None):
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    movements, aging = _collect_summary_data(
        sku=sku, location_code=location_code, start_date=start_date, end_date=end_date
    )

    return jsonify({
        "movement_trends": movements,
        "stock_aging": aging,
    })


@bp.route("/export")
@login_required
def export_summary():
    sku = request.args.get("sku", "").strip() or None
    location_code = request.args.get("location", "").strip() or None
    start_date = _parse_date_param(request.args.get("start"))
    end_date = _parse_date_param(request.args.get("end"))

    movements, aging = _collect_summary_data(
        sku=sku, location_code=location_code, start_date=start_date, end_date=end_date
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        aging_stream = io.StringIO()
        aging_writer = csv.writer(aging_stream)
        aging_writer.writerow(["sku", "lot_number", "quantity", "days"])
        for row in aging:
            aging_writer.writerow([
                row["sku"] or "",
                row["lot_number"],
                row["quantity"],
                row["days"],
            ])
        zf.writestr("stock_aging.csv", aging_stream.getvalue())

        movement_stream = io.StringIO()
        movement_writer = csv.writer(movement_stream)
        movement_writer.writerow(["date", "movement_type", "quantity", "location"])
        for row in movements:
            movement_writer.writerow([
                row["date"],
                row["movement_type"],
                row["quantity"],
                row["location"] or "",
            ])
        zf.writestr("movement_trends.csv", movement_stream.getvalue())

    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=report_summary.zip"},
    )
