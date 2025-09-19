import csv
import io
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, Response, abort, jsonify, render_template, request
from sqlalchemy import case, func

from invapp.models import Batch, Item, Location, Movement, db

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        abort(400, "Invalid date format. Use YYYY-MM-DD.")


def _movement_query(sku: str, location_code: str | None, start_at, end_at):
    query = (
        db.session.query(
            Movement.date,
            Movement.movement_type,
            Movement.quantity,
            Location.code.label("location_code"),
            Batch.lot_number,
        )
        .join(Item, Movement.item_id == Item.id)
        .outerjoin(Location, Movement.location_id == Location.id)
        .outerjoin(Batch, Movement.batch_id == Batch.id)
        .filter(Item.sku == sku)
    )
    if location_code:
        query = query.filter(Location.code == location_code)
    if start_at:
        query = query.filter(Movement.date >= start_at)
    if end_at:
        query = query.filter(Movement.date < end_at)
    return query.order_by(Movement.date.asc()).all()


def _stock_aging_rows(sku: str):
    batches = (
        db.session.query(Batch)
        .join(Item, Batch.item_id == Item.id)
        .filter(Item.sku == sku)
        .order_by(Batch.received_date.desc())
        .all()
    )
    today = datetime.utcnow().date()
    rows = []
    for batch in batches:
        if batch.received_date is None:
            continue
        days_old = (today - batch.received_date.date()).days
        rows.append(
            {
                "sku": batch.item.sku if batch.item else sku,
                "lot_number": batch.lot_number,
                "quantity": batch.quantity,
                "days": days_old,
            }
        )
    return rows

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


@bp.route("/summary_data")
def summary_data():
    sku = (request.args.get("sku") or "").strip()
    if not sku:
        abort(400, "sku parameter is required")

    location_code = (request.args.get("location") or "").strip() or None
    start_at = _parse_date(request.args.get("start"))
    end_at = _parse_date(request.args.get("end"))
    if end_at:
        end_at = end_at + timedelta(days=1)

    movements = _movement_query(sku, location_code, start_at, end_at)
    movement_rows = [
        {
            "date": movement.date.strftime("%Y-%m-%d"),
            "quantity": movement.quantity,
            "movement_type": movement.movement_type,
            "location": movement.location_code,
            "lot_number": movement.lot_number,
        }
        for movement in movements
    ]

    stock_aging = _stock_aging_rows(sku)

    return jsonify({
        "movement_trends": movement_rows,
        "stock_aging": stock_aging,
    })


@bp.route("/export")
def export_summary():
    sku = (request.args.get("sku") or "").strip()
    if not sku:
        abort(400, "sku parameter is required")

    location_code = (request.args.get("location") or "").strip() or None
    start_at = _parse_date(request.args.get("start"))
    end_at = _parse_date(request.args.get("end"))
    if end_at:
        end_at = end_at + timedelta(days=1)

    movements = _movement_query(sku, location_code, start_at, end_at)
    stock_aging = _stock_aging_rows(sku)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Stock aging CSV
        aging_output = io.StringIO()
        aging_writer = csv.writer(aging_output)
        aging_writer.writerow(["sku", "lot_number", "quantity", "days"])
        for row in stock_aging:
            aging_writer.writerow(
                [row["sku"], row["lot_number"] or "", row["quantity"], row["days"]]
            )
        zf.writestr("stock_aging.csv", aging_output.getvalue())

        # Movement trends CSV
        movement_output = io.StringIO()
        movement_writer = csv.writer(movement_output)
        movement_writer.writerow(["date", "movement_type", "quantity", "location", "lot_number"])
        for movement in movements:
            movement_writer.writerow(
                [
                    movement.date.strftime("%Y-%m-%d"),
                    movement.movement_type,
                    movement.quantity,
                    movement.location_code or "",
                    movement.lot_number or "",
                ]
            )
        zf.writestr("movement_trends.csv", movement_output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=summary_reports.zip"},
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
