from flask import Blueprint, render_template, request, redirect, url_for, flash
from invapp.models import db, Item, Order, OrderItem, BOMComponent, RoutingStep, Reservation

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


@bp.route("/open")
def open_orders():
    orders = Order.query.filter_by(status="OPEN").all()
    return render_template("orders/open.html", orders=orders)


@bp.route("/closed")
def closed_orders():
    orders = Order.query.filter_by(status="CLOSED").all()
    return render_template("orders/closed.html", orders=orders)


@bp.route("/new", methods=["GET", "POST"])
def new_order():
    if request.method == "POST":
        order_number = request.form["order_number"].strip()
        item_id = int(request.form["item_id"])
        quantity = int(request.form["quantity"])
        bom_str = request.form.get("bom", "").strip()
        steps_str = request.form.get("steps", "").strip()

        order = Order(order_number=order_number, status="OPEN")
        db.session.add(order)

        order_item = OrderItem(order=order, item_id=item_id, quantity=quantity)
        db.session.add(order_item)

        # Parse BOM components: "item_id:qty,item_id:qty"
        if bom_str:
            for pair in bom_str.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                try:
                    comp_id_str, qty_str = pair.split(":")
                    comp_id = int(comp_id_str)
                    qty = int(qty_str)
                except ValueError:
                    db.session.rollback()
                    flash("Invalid BOM format", "danger")
                    return redirect(url_for("orders.new_order"))
                comp_item = Item.query.get(comp_id)
                if not comp_item:
                    db.session.rollback()
                    flash(f"Invalid BOM component item id {comp_id}", "danger")
                    return redirect(url_for("orders.new_order"))
                bom = BOMComponent(order_item=order_item, component_item_id=comp_id, quantity=qty)
                db.session.add(bom)
                reservation = Reservation(order_item=order_item, item_id=comp_id, quantity=qty)
                db.session.add(reservation)

        # Parse routing steps
        if steps_str:
            lines = [line.strip() for line in steps_str.splitlines() if line.strip()]
            for idx, desc in enumerate(lines, start=1):
                step = RoutingStep(order=order, step_number=idx, description=desc)
                db.session.add(step)

        db.session.commit()
        flash("Order created", "success")
        return redirect(url_for("orders.open_orders"))

    items = Item.query.all()
    return render_template("orders/new.html", items=items)


@bp.route("/<int:order_id>")
def view_order(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("orders/view.html", order=order)


@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    if request.method == "POST":
        order.status = request.form["status"].strip()
        db.session.commit()
        flash("Order updated", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))
    return render_template("orders/edit.html", order=order)


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    flash("Order deleted", "success")
    return redirect(url_for("orders.open_orders"))
