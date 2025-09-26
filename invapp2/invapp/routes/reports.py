import csv
import io
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from flask import Blueprint, Response, jsonify, render_template, request
from invapp.auth import blueprint_page_guard
from sqlalchemy import case, func
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import Batch, Item, Location, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")


bp.before_request(blueprint_page_guard("reports"))


def _decimal_to_string(value):
    if value is None:
        return ""
    return f"{Decimal(value):.2f}"


def _parse_date_param(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _movement_trends(
    sku: Optional[str],
    location: Optional[str],
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
):
    query = (
        db.session.query(Movement)
        .options(joinedload(Movement.item), joinedload(Movement.location))
        .order_by(Movement.date.asc())
    )
    if sku:
        query = query.filter(Movement.item.has(Item.sku == sku))
    if location:
        query = query.filter(Movement.location.has(Location.code == location))
    if start_dt:
        query = query.filter(Movement.date >= start_dt)
    if end_dt:
        query = query.filter(Movement.date <= end_dt)

    rows = []
    for movement in query.all():
        item = movement.item
        location_obj = movement.location
        rows.append(
            {
                "date": movement.date.strftime("%Y-%m-%d") if movement.date else None,
                "sku": item.sku if item else None,
                "item_name": item.name if item else None,
                "movement_type": movement.movement_type,
                "quantity": int(movement.quantity or 0),
                "location": location_obj.code if location_obj else None,
            }
        )
    return rows


def _stock_aging(sku: Optional[str]):
    query = db.session.query(Batch).options(joinedload(Batch.item))
    if sku:
        query = query.filter(Batch.item.has(Item.sku == sku))

    today = datetime.utcnow().date()
    rows = []
    for batch in query.all():
        item = batch.item
        received_date = batch.received_date.date() if batch.received_date else today
        days = max((today - received_date).days, 0)
        rows.append(
            {
                "sku": item.sku if item else None,
                "lot_number": batch.lot_number,
                "quantity": int(batch.quantity or 0),
                "days": days,
            }
        )
    return rows


def _rows_to_csv(rows: Iterable[dict], headers: list[str]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([
            "" if row.get(header) is None else row.get(header)
            for header in headers
        ])
    return output.getvalue()


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
    sku = request.args.get("sku") or None
    location = request.args.get("location") or None
    start_dt = _parse_date_param(request.args.get("start"))
    end_dt = _parse_date_param(request.args.get("end"))

    movements = _movement_trends(sku, location, start_dt, end_dt)
    aging = _stock_aging(sku)

    return jsonify({"movement_trends": movements, "stock_aging": aging})


@bp.route("/export")
def export():
    sku = request.args.get("sku") or None
    location = request.args.get("location") or None
    start_dt = _parse_date_param(request.args.get("start"))
    end_dt = _parse_date_param(request.args.get("end"))

    movements = _movement_trends(sku, location, start_dt, end_dt)
    aging = _stock_aging(sku)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "stock_aging.csv",
            _rows_to_csv(aging, ["sku", "lot_number", "quantity", "days"]),
        )
        zf.writestr(
            "movement_trends.csv",
            _rows_to_csv(
                movements,
                ["date", "sku", "movement_type", "quantity", "location"],
            ),
        )

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports.zip"},
    )


@bp.route("/generate")
def generate_reports():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
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

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["code", "description"])
        for location in Location.query.all():
            writer.writerow([location.code, location.description])
        zf.writestr("locations.csv", output.getvalue())

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "item_sku", "lot_number", "quantity"])
        for batch in Batch.query.all():
            writer.writerow(
                [
                    batch.id,
                    batch.item.sku if batch.item else "?",
                    batch.lot_number,
                    batch.quantity,
                ]
            )
        zf.writestr("batches.csv", output.getvalue())

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "date",
                "sku",
                "item_name",
                "movement_type",
                "quantity",
                "location",
                "lot_number",
                "person",
                "reference",
                "po_number",
            ]
        )
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
            move_date,
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
            writer.writerow(
                [
                    move_date.strftime("%Y-%m-%d %H:%M"),
                    sku,
                    item_name,
                    movement_type,
                    quantity,
                    location_code,
                    lot_number or "-",
                    person or "-",
                    reference or "-",
                    po_number or "-",
                ]
            )
        zf.writestr("movements.csv", output.getvalue())

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports.zip"},
    )
