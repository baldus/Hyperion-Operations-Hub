"""Order related routes."""

from flask import Blueprint, render_template, request, redirect, url_for, flash

from invapp.models import db, Item, Order, OrderStep

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


# Example routing data. In a real application this would come from dedicated
# routing/BOM tables. The mapping is SKU -> list of step names.
ROUTING_DATA = {}


def get_routing_steps(sku: str):
    """Return routing steps for a given SKU.

    If no routing information exists, a simple three step default is returned.
    """

    return ROUTING_DATA.get(sku, ["Build", "Inspect", "Pack"])


@bp.route("/new", methods=["GET", "POST"])
def create_order():
    """Create a new production order."""

    items = Item.query.order_by(Item.sku).all()
    errors = []

    if request.method == "POST":
        sku = request.form.get("sku", "").strip()
        qty_raw = request.form.get("quantity", "").strip()

        item = Item.query.filter_by(sku=sku).first()
        try:
            quantity = int(qty_raw)
        except (TypeError, ValueError):
            quantity = None

        if not item:
            errors.append("Invalid SKU selected.")
        if not quantity or quantity <= 0:
            errors.append("Quantity must be a positive integer.")

        if not errors:
            order = Order(item_id=item.id, quantity=quantity)
            db.session.add(order)

            # Generate order steps from routing data
            for seq, step_name in enumerate(get_routing_steps(item.sku), start=1):
                db.session.add(
                    OrderStep(order=order, sequence=seq, name=step_name)
                )

            db.session.commit()
            flash("Order created successfully", "success")
            return redirect(url_for("orders.orders_home"))

    return render_template("orders/new.html", items=items, errors=errors)
