from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy.orm import joinedload
from invapp.models import db, Order, OrderStep, OrderStepItem

bp = Blueprint("orders", __name__, url_prefix="/orders")

@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


@bp.route("/process/<step_name>", methods=["GET", "POST"])
def process_step(step_name):
    if request.method == "POST":
        step_id = int(request.form["step_id"])
        step = OrderStep.query.get_or_404(step_id)
        step.status = "COMPLETE"
        step.completed_at = datetime.utcnow()

        next_step = (
            OrderStep.query.filter(
                OrderStep.order_id == step.order_id,
                OrderStep.sequence > step.sequence,
            )
            .order_by(OrderStep.sequence)
            .first()
        )
        if next_step and next_step.status == "PENDING":
            next_step.status = "WAITING"
            next_step.activated_at = datetime.utcnow()

        db.session.commit()
        flash(f"Step '{step.step_name}' completed for order {step.order.code}.", "success")
        return redirect(url_for("orders.process_step", step_name=step_name))

    steps = (
        OrderStep.query.filter_by(step_name=step_name, status="WAITING")
        .order_by(OrderStep.sequence)
        .options(
            joinedload(OrderStep.order),
            joinedload(OrderStep.items).joinedload(OrderStepItem.item),
        )
        .all()
    )

    return render_template(
        "orders/process_step.html", step_name=step_name, steps=steps
    )
