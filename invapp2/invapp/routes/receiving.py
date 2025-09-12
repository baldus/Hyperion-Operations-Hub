from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    Response,
)
from invapp.extensions import db
from invapp.models import Receiving, Item, Stock, Location
from invapp.printing.zebra import (
    print_receiving_label,
    render_receiving_label_png,
)

bp = Blueprint("receiving", __name__, url_prefix="/receiving")

@bp.route("/")
def receiving_home():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    pagination = (
        Receiving.query.order_by(Receiving.date_received.desc())
        .paginate(page=page, per_page=size, error_out=False)
    )
    return render_template(
        "receiving/home.html",
        records=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
    )

@bp.route("/add", methods=["GET", "POST"])
def add_receiving():
    locations = Location.query.all()

    if request.method == "POST":
        sku = request.form["sku"].strip()
        qty = int(request.form["qty"])
        person = request.form["person"].strip()
        po_number = request.form.get("po_number", "").strip()
        location_id = int(request.form["location_id"])

        # Check if SKU exists
        item = Item.query.filter_by(sku=sku).first()
        if not item:
            msg = f"Item with SKU {sku} not found."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": msg}), 400
            flash(msg, "error")
            return redirect(url_for("receiving.add_receiving"))

        # Log receiving
        receiving = Receiving(
            item_id=item.id,
            location_id=location_id,
            quantity=qty,
            person=person,
            po_number=po_number,
        )
        db.session.add(receiving)

        # Update stock for this item/location
        stock = Stock.query.filter_by(item_id=item.id, location_id=location_id).first()
        if stock:
            stock.quantity += qty
        else:
            stock = Stock(item_id=item.id, location_id=location_id, quantity=qty)
            db.session.add(stock)

        db.session.commit()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            label_url = url_for(
                "receiving.label_preview",
                sku=item.sku,
                description=item.name,
                qty=qty,
            )
            return jsonify(
                {
                    "success": True,
                    "label_url": label_url,
                    "sku": item.sku,
                    "description": item.name,
                    "qty": qty,
                }
            )

        flash("Receiving recorded and stock updated!", "success")
        return redirect(url_for("receiving.receiving_home"))

    return render_template("receiving/add.html", locations=locations)


@bp.route("/label-preview")
def label_preview():
    sku = request.args["sku"]
    description = request.args["description"]
    qty = int(request.args["qty"])
    image = render_receiving_label_png(sku, description, qty)
    return Response(image, mimetype="image/png")


@bp.route("/print-label", methods=["POST"])
def print_label():
    data = request.get_json() or {}
    sku = data.get("sku")
    description = data.get("description")
    qty = int(data.get("qty", 0))
    copies = int(data.get("copies", 1))

    success = True
    for _ in range(copies):
        success = print_receiving_label(sku, description, qty) and success

    return jsonify({"printed": success})
