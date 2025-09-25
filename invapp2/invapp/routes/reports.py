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


def _parse_date_param(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _build_summary(
    sku: str | None,
    location_code: str | None,
    start_date: datetime | None,
    end_date: datetime | None,
):
    movement_query = (
        db.session.query(
            Movement.date,
            Movement.movement_type,
            Movement.quantity,
            Item.sku,
            Location.code,
        )
        .join(Item, Movement.item_id == Item.id)
        .join(Location, Movement.location_id == Location.id)
    )

    if sku:
        movement_query = movement_query.filter(Item.sku == sku)
    if location_code:
        movement_query = movement_query.filter(Location.code == location_code)
    if start_date:
        movement_query = movement_query.filter(Movement.date >= start_date)
    if end_date:
        movement_query = movement_query.filter(Movement.date <= end_date)

    movement_rows = [
        {
            "date": movement_date.strftime("%Y-%m-%d"),
            "movement_type": movement_type,
            "quantity": quantity,
            "sku": sku_value,
            "location": location_value,
        }
        for movement_date, movement_type, quantity, sku_value, location_value in movement_query.order_by(Movement.date.asc()).all()
    ]

    aging_query = db.session.query(Batch, Item).join(Item, Batch.item_id == Item.id)
    if sku:
        aging_query = aging_query.filter(Item.sku == sku)

    aging_rows = []
    today = datetime.utcnow().date()
    for batch, item in aging_query.all():
        if location_code:
            has_location = (
                db.session.query(Movement.id)
                .join(Location, Movement.location_id == Location.id)
                .filter(Movement.batch_id == batch.id, Location.code == location_code)
                .first()
            )
            if not has_location:
                continue
        days_old = (today - batch.received_date.date()).days if batch.received_date else 0
        aging_rows.append(
            {
                "sku": item.sku,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "days": days_old,
            }
        )

    return movement_rows, aging_rows

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

@bp.route("/summary_data")
@login_required
def summary_data():
    sku = request.args.get("sku")
    location_code = request.args.get("location")
    start_date = _parse_date_param(request.args.get("start"))
    end_date = _parse_date_param(request.args.get("end"))

    movement_rows, aging_rows = _build_summary(sku, location_code, start_date, end_date)
    return jsonify({"movement_trends": movement_rows, "stock_aging": aging_rows})


@bp.route("/export")
@login_required
def export_summary():
    sku = request.args.get("sku")
    location_code = request.args.get("location")
    start_date = _parse_date_param(request.args.get("start"))
    end_date = _parse_date_param(request.args.get("end"))

    movement_rows, aging_rows = _build_summary(sku, location_code, start_date, end_date)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        movement_output = io.StringIO()
        movement_writer = csv.writer(movement_output)
        movement_writer.writerow(["date", "sku", "location", "movement_type", "quantity"])
        for row in movement_rows:
            movement_writer.writerow(
                [row["date"], row["sku"], row["location"], row["movement_type"], row["quantity"]]
            )
        zf.writestr("movement_trends.csv", movement_output.getvalue())

        aging_output = io.StringIO()
        aging_writer = csv.writer(aging_output)
        aging_writer.writerow(["sku", "lot_number", "quantity", "days"])
        for row in aging_rows:
            aging_writer.writerow(
                [row["sku"], row["lot_number"] or "", row["quantity"], row["days"]]
            )
        zf.writestr("stock_aging.csv", aging_output.getvalue())

    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=summary.zip"},
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
