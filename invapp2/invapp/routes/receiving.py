from flask import Blueprint, render_template, request, redirect, url_for, flash
from invapp.extensions import db
from invapp.models import Receiving, Item, Stock, Location

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
            flash(f"Item with SKU {sku} not found.", "error")
            return redirect(url_for("receiving.add_receiving"))

        # Log receiving
        receiving = Receiving(
            item_id=item.id,
            location_id=location_id,
            quantity=qty,
            person=person,
            po_number=po_number
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
        flash("Receiving recorded and stock updated!", "success")
        return redirect(url_for("receiving.receiving_home"))

    return render_template("receiving/add.html", locations=locations)
