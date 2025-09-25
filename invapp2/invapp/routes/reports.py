import csv
import io
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    session,
)
from flask_login import current_user
from sqlalchemy import case, func

from invapp.extensions import login_manager
from invapp.models import Batch, Item, Location, Movement, db

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.before_request
def require_report_access():
    if session.get("is_admin"):
        return None
    if current_user.is_authenticated:
        return None
    return login_manager.unauthorized()


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _collect_report_data(
    sku: str | None,
    location_code: str | None,
    start: str | None,
    end: str | None,
) -> dict[str, list[dict[str, object]]]:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    start_dt = (
        datetime.combine(start_date, datetime.min.time())
        if start_date
        else None
    )
    end_dt = (
        datetime.combine(end_date, datetime.max.time())
        if end_date
        else None
    )

    movement_query = (
        db.session.query(
            Movement.date,
            Movement.quantity,
            Movement.movement_type,
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
    if start_dt:
        movement_query = movement_query.filter(Movement.date >= start_dt)
    if end_dt:
        movement_query = movement_query.filter(Movement.date <= end_dt)

    movement_rows = []
    for movement_date, quantity, movement_type, item_sku, loc_code in (
        movement_query.order_by(Movement.date.asc()).all()
    ):
        movement_rows.append(
            {
                "date": movement_date.strftime("%Y-%m-%d"),
                "quantity": int(quantity or 0),
                "movement_type": movement_type,
                "sku": item_sku,
                "location": loc_code,
            }
        )

    stock_rows = []
    today = datetime.utcnow().date()
    batch_query = db.session.query(Batch).join(Item)
    if sku:
        batch_query = batch_query.filter(Item.sku == sku)
    for batch in batch_query.all():
        received_date = batch.received_date.date() if batch.received_date else None
        days_on_hand = (
            (today - received_date).days if received_date is not None else None
        )
        stock_rows.append(
            {
                "sku": batch.item.sku if batch.item else "",
                "lot_number": batch.lot_number or "",
                "quantity": int(batch.quantity or 0),
                "received_date": received_date.isoformat() if received_date else None,
                "days": days_on_hand,
            }
        )

    return {
        "movement_trends": movement_rows,
        "stock_aging": stock_rows,
    }


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


@bp.route("/summary_data")
def summary_data():
    sku = request.args.get("sku")
    location_code = request.args.get("location")
    start = request.args.get("start")
    end = request.args.get("end")
    data = _collect_report_data(sku, location_code, start, end)
    return jsonify(data)


@bp.route("/export")
def export():
    sku = request.args.get("sku")
    location_code = request.args.get("location")
    start = request.args.get("start")
    end = request.args.get("end")
    data = _collect_report_data(sku, location_code, start, end)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        stock_output = io.StringIO()
        stock_writer = csv.writer(stock_output)
        stock_writer.writerow(
            ["sku", "lot_number", "quantity", "received_date", "days_on_hand"]
        )
        for row in data["stock_aging"]:
            stock_writer.writerow(
                [
                    row["sku"],
                    row["lot_number"],
                    row["quantity"],
                    row["received_date"] or "",
                    row["days"] if row["days"] is not None else "",
                ]
            )
        zf.writestr("stock_aging.csv", stock_output.getvalue())

        movement_output = io.StringIO()
        movement_writer = csv.writer(movement_output)
        movement_writer.writerow(
            ["date", "sku", "movement_type", "quantity", "location"]
        )
        for row in data["movement_trends"]:
            movement_writer.writerow(
                [
                    row["date"],
                    row["sku"],
                    row["movement_type"],
                    row["quantity"],
                    row["location"],
                ]
            )
        zf.writestr("movement_trends.csv", movement_output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=report_export.zip"},
    )
