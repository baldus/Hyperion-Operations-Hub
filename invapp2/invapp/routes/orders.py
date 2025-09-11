from flask import Blueprint, render_template, request, jsonify
from invapp.models import db, WorkOrder, Reservation, Movement

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


@bp.route("/create", methods=["POST"])
def create_order():
    """Create a work order and reserve inventory for its BOM components."""

    data = request.get_json() or {}
    description = data.get("description")
    components = data.get("components", [])

    order = WorkOrder(description=description)
    db.session.add(order)
    db.session.flush()  # ensures order.id available

    for comp in components:
        res = Reservation(
            order_id=order.id,
            item_id=comp["item_id"],
            batch_id=comp.get("batch_id"),
            location_id=comp["location_id"],
            quantity=comp["quantity"],
        )
        db.session.add(res)

    db.session.commit()
    return jsonify({"order_id": order.id}), 201


@bp.route("/<int:order_id>/complete", methods=["POST"])
def complete_order(order_id):
    """Mark reservations as consumed and create movements to reduce stock."""

    order = WorkOrder.query.get_or_404(order_id)
    reservations = Reservation.query.filter_by(order_id=order.id, consumed=False).all()

    for res in reservations:
        mv = Movement(
            item_id=res.item_id,
            batch_id=res.batch_id,
            location_id=res.location_id,
            quantity=-res.quantity,
            movement_type="ISSUE",
            reference=f"WO {order.id} consumption",
        )
        db.session.add(mv)
        res.consumed = True

    order.completed = True
    db.session.commit()
    return jsonify({"status": "completed"})
