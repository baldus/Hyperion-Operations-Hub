from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import (
    Item,
    Order,
    OrderBOMComponent,
    OrderItem,
    OrderStatus,
    OrderStep,
    Reservation,
)

bp = Blueprint("orders", __name__, url_prefix="/orders")


def _search_filter(query, search_term):
    if not search_term:
        return query

    like_term = f"%{search_term}%"
    return query.join(Order.items).join(OrderItem.item).filter(
        or_(
            Order.order_number.ilike(like_term),
            Item.sku.ilike(like_term),
            Item.name.ilike(like_term),
        )
    )


@bp.route("/")
def orders_home():
    search_term = request.args.get("q", "").strip()
    query = Order.query.options(
        joinedload(Order.items).joinedload(OrderItem.item),
        joinedload(Order.steps),
    ).filter(Order.status.in_(OrderStatus.ACTIVE_STATES))
    query = _search_filter(query, search_term)
    open_orders = query.order_by(Order.promised_date.is_(None), Order.promised_date, Order.order_number).all()
    return render_template("orders/home.html", orders=open_orders, search_term=search_term)


@bp.route("/open")
def view_open_orders():
    orders = (
        Order.query.options(joinedload(Order.items).joinedload(OrderItem.item))
        .filter(Order.status == OrderStatus.OPEN)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/open.html", orders=orders)


@bp.route("/closed")
def view_closed_orders():
    orders = (
        Order.query.options(joinedload(Order.items).joinedload(OrderItem.item))
        .filter(Order.status == OrderStatus.CLOSED)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/closed.html", orders=orders)


@bp.route("/new", methods=["GET", "POST"])
def new_order():
    items = Item.query.order_by(Item.sku).all()
    if request.method == "POST":
        order_number = (request.form.get("order_number") or "").strip()
        item_id = request.form.get("item_id")
        quantity_raw = (request.form.get("quantity") or "").strip()
        bom_raw = (request.form.get("bom") or "").strip()
        steps_raw = request.form.get("steps") or ""

        if not order_number:
            flash("Order number is required", "danger")
            return render_template("orders/new.html", items=items)

        if Order.query.filter_by(order_number=order_number).first():
            flash("Order number already exists", "danger")
            return render_template("orders/new.html", items=items)

        try:
            item_id = int(item_id)
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            flash("Item and quantity are required", "danger")
            return render_template("orders/new.html", items=items)

        if quantity <= 0:
            flash("Quantity must be greater than zero", "danger")
            return render_template("orders/new.html", items=items)

        finished_good = Item.query.get(item_id)
        if finished_good is None:
            flash("Selected item does not exist", "danger")
            return render_template("orders/new.html", items=items)

        bom_components = []
        if bom_raw:
            for token in bom_raw.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    component_str, qty_str = token.split(":", 1)
                    component_id = int(component_str.strip())
                    component_qty = int(qty_str.strip())
                except ValueError:
                    flash("Invalid BOM format. Use item_id:qty", "danger")
                    return render_template("orders/new.html", items=items)

                component_item = Item.query.get(component_id)
                if component_item is None:
                    flash("Invalid BOM component item id", "danger")
                    return render_template("orders/new.html", items=items)

                if component_qty <= 0:
                    flash("BOM component quantity must be positive", "danger")
                    return render_template("orders/new.html", items=items)

                bom_components.append((component_item, component_qty))

        steps = [line.strip() for line in steps_raw.splitlines() if line.strip()]

        order = Order(order_number=order_number)
        order_item = OrderItem(order=order, item_id=finished_good.id, quantity=quantity)
        db.session.add(order)

        for component_item, component_qty in bom_components:
            bom_entry = OrderBOMComponent(
                order_item=order_item,
                component_item_id=component_item.id,
                quantity=component_qty,
            )
            db.session.add(bom_entry)
            db.session.add(
                Reservation(
                    order_item=order_item,
                    item_id=component_item.id,
                    quantity=component_qty * quantity,
                )
            )

        for idx, description in enumerate(steps, start=1):
            db.session.add(
                OrderStep(order=order, sequence=idx, description=description)
            )

        db.session.commit()
        flash("Order created", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template("orders/new.html", items=items)


@bp.route("/<int:order_id>")
def view_order(order_id):
    order = (
        Order.query.options(
            joinedload(Order.items)
            .joinedload(OrderItem.bom_components)
            .joinedload(OrderBOMComponent.component_item),
            joinedload(Order.items).joinedload(OrderItem.item),
            joinedload(Order.items)
            .joinedload(OrderItem.reservations)
            .joinedload(Reservation.item),
            joinedload(Order.steps),
        )
        .filter_by(id=order_id)
        .first_or_404()
    )
    return render_template("orders/view.html", order=order)


@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    if request.method == "POST":
        status = request.form.get("status", order.status)
        if status not in {OrderStatus.OPEN, OrderStatus.CLOSED, OrderStatus.CANCELLED}:
            flash("Invalid status", "danger")
            return render_template("orders/edit.html", order=order)

        order.status = status
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
    return redirect(url_for("orders.orders_home"))
