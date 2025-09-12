import io, csv, zipfile
from flask import Blueprint, Response, render_template
from invapp.models import db, Item, Location, Batch, Movement

bp = Blueprint("reports", __name__, url_prefix="/reports")

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
