import io, csv, zipfile
from flask import Blueprint, Response, render_template
from flask_login import login_required

from invapp.models import db, Item, Location, Batch, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.before_request
@login_required
def require_login():
    pass

@bp.route("/")
def reports_home():
    return render_template("reports/home.html")

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

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports.zip"}
    )
