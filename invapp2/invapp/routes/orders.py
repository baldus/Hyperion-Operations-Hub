import json
from datetime import datetime

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import (
    Item,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    Reservation,
    RoutingStep,
    RoutingStepComponent,
)

bp = Blueprint("orders", __name__, url_prefix="/orders")


def _search_filter(query, search_term):
    if not search_term:
        return query

    like_term = f"%{search_term}%"
    return query.join(Order.order_lines).join(OrderLine.item).filter(
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
        joinedload(Order.order_lines).joinedload(OrderLine.item),
        joinedload(Order.routing_steps),
    ).filter(Order.status.in_(OrderStatus.ACTIVE_STATES))
    query = _search_filter(query, search_term)
    open_orders = query.order_by(Order.promised_date.is_(None), Order.promised_date, Order.order_number).all()
    return render_template("orders/home.html", orders=open_orders, search_term=search_term)


@bp.route("/open")
def view_open_orders():
    orders = (
        Order.query.options(joinedload(Order.order_lines).joinedload(OrderLine.item))
        .filter(Order.status == OrderStatus.OPEN)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/open.html", orders=orders)


@bp.route("/closed")
def view_closed_orders():
    orders = (
        Order.query.options(joinedload(Order.order_lines).joinedload(OrderLine.item))
        .filter(Order.status == OrderStatus.CLOSED)
        .order_by(Order.order_number)
        .all()
    )
    return render_template("orders/closed.html", orders=orders)


def _parse_date(raw_value, field_label, errors):
    if not raw_value:
        errors.append(f"{field_label} is required.")
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{field_label} must be a valid date (YYYY-MM-DD).")
        return None


@bp.route("/new", methods=["GET", "POST"])
def new_order():
    if not session.get("is_admin"):
        next_target = request.full_path if request.query_string else request.path
        flash("Administrator access is required to create new orders.", "danger")
        return redirect(url_for("admin.login", next=next_target))

    items = Item.query.order_by(Item.sku).all()
    form_data = {
        "order_number": "",
        "finished_good_sku": "",
        "quantity": "",
        "customer_name": "",
        "created_by": "",
        "promised_date": "",
        "scheduled_start_date": "",
        "scheduled_completion_date": "",
        "bom": [],
        "steps": [],
    }

    if request.method == "POST":
        errors = []
        order_number = (request.form.get("order_number") or "").strip()
        finished_good_sku = (request.form.get("finished_good_sku") or "").strip()
        quantity_raw = (request.form.get("quantity") or "").strip()
        customer_name = (request.form.get("customer_name") or "").strip()
        created_by = (request.form.get("created_by") or "").strip()
        promised_date_raw = (request.form.get("promised_date") or "").strip()
        scheduled_start_raw = (
            request.form.get("scheduled_start_date") or ""
        ).strip()
        scheduled_completion_raw = (
            request.form.get("scheduled_completion_date") or ""
        ).strip()
        bom_raw = request.form.get("bom_data") or "[]"
        routing_raw = request.form.get("routing_data") or "[]"

        form_data.update(
            {
                "order_number": order_number,
                "finished_good_sku": finished_good_sku,
                "quantity": quantity_raw,
                "customer_name": customer_name,
                "created_by": created_by,
                "promised_date": promised_date_raw,
                "scheduled_start_date": scheduled_start_raw,
                "scheduled_completion_date": scheduled_completion_raw,
            }
        )

        if not order_number:
            errors.append("Order number is required.")
        elif Order.query.filter_by(order_number=order_number).first():
            errors.append("Order number already exists.")

        if not customer_name:
            errors.append("Customer name is required.")

        if not created_by:
            errors.append("Order creator name is required.")

        finished_good = None
        if not finished_good_sku:
            errors.append("Finished good part number is required.")
        else:
            finished_good = Item.query.filter_by(sku=finished_good_sku).first()
            if finished_good is None:
                errors.append(
                    f"Finished good part number '{finished_good_sku}' was not found."
                )

        try:
            quantity = int(quantity_raw)
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Quantity must be a positive integer.")
            quantity = None

        promised_date = _parse_date(promised_date_raw, "Promised ship date", errors)
        scheduled_start_date = _parse_date(
            scheduled_start_raw, "Scheduled start date", errors
        )
        scheduled_completion_date = _parse_date(
            scheduled_completion_raw, "Scheduled completion date", errors
        )

        if (
            scheduled_start_date
            and scheduled_completion_date
            and scheduled_start_date > scheduled_completion_date
        ):
            errors.append("Scheduled start date must be on or before completion date.")

        if (
            promised_date
            and scheduled_completion_date
            and promised_date < scheduled_completion_date
        ):
            errors.append(
                "Promised ship date must be on or after the scheduled completion date."
            )

        try:
            bom_payload = json.loads(bom_raw)
            if not isinstance(bom_payload, list):
                raise ValueError
        except ValueError:
            bom_payload = []
            errors.append("Unable to read the BOM component details submitted.")

        try:
            routing_payload = json.loads(routing_raw)
            if not isinstance(routing_payload, list):
                raise ValueError
        except ValueError:
            routing_payload = []
            errors.append("Unable to read the routing information submitted.")

        form_data["bom"] = bom_payload
        form_data["steps"] = routing_payload

        bom_components = []
        component_lookup = {}
        component_skus_seen = set()
        if not bom_payload:
            errors.append("At least one BOM component is required.")
        else:
            for entry in bom_payload:
                if not isinstance(entry, dict):
                    errors.append("Each BOM component must include a SKU and quantity.")
                    continue
                sku = (entry.get("sku") or "").strip()
                quantity_value = entry.get("quantity")
                if not sku:
                    errors.append("BOM components require a component SKU.")
                    continue
                if sku in component_skus_seen:
                    errors.append(f"BOM component {sku} is listed more than once.")
                    continue
                component_item = Item.query.filter_by(sku=sku).first()
                if component_item is None:
                    errors.append(f"BOM component SKU '{sku}' was not found.")
                    continue
                try:
                    component_quantity = int(quantity_value)
                    if component_quantity <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(
                        f"BOM component quantity for {sku} must be a positive integer."
                    )
                    continue

                component_entry = {
                    "sku": sku,
                    "item": component_item,
                    "quantity": component_quantity,
                }
                bom_components.append(component_entry)
                component_lookup[sku] = component_entry
                component_skus_seen.add(sku)

        routing_steps = []
        referenced_components = set()
        sequences_seen = set()
        if not routing_payload:
            errors.append("At least one routing step is required.")
        else:
            for entry in routing_payload:
                if not isinstance(entry, dict):
                    errors.append("Invalid routing step definition submitted.")
                    continue

                raw_sequence = entry.get("sequence")
                try:
                    sequence = int(raw_sequence)
                except (TypeError, ValueError):
                    errors.append("Routing step sequences must be whole numbers.")
                    continue
                if sequence in sequences_seen:
                    errors.append(
                        f"Routing step sequence {sequence} is defined more than once."
                    )
                    continue
                sequences_seen.add(sequence)

                work_cell = (entry.get("work_cell") or "").strip()
                instructions = (entry.get("instructions") or "").strip()
                if not instructions:
                    errors.append(
                        f"Routing step {sequence} must include work instructions."
                    )

                component_values = entry.get("components") or []
                if not isinstance(component_values, list):
                    errors.append(
                        f"Component usage for routing step {sequence} is not valid."
                    )
                    component_values = []

                resolved_components = []
                for sku in component_values:
                    if sku not in component_lookup:
                        errors.append(
                            f"Routing step {sequence} references unknown component {sku}."
                        )
                        continue
                    if sku in resolved_components:
                        continue
                    resolved_components.append(sku)
                    referenced_components.add(sku)

                routing_steps.append(
                    {
                        "sequence": sequence,
                        "work_cell": work_cell,
                        "instructions": instructions,
                        "components": resolved_components,
                    }
                )

        missing_component_usage = set(component_lookup) - referenced_components
        if missing_component_usage:
            missing_list = ", ".join(sorted(missing_component_usage))
            errors.append(
                "Each BOM component must be associated with at least one routing step. "
                f"Missing usage for: {missing_list}."
            )

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "orders/new.html", items=items, form_data=form_data
            )

        order = Order(
            order_number=order_number,
            customer_name=customer_name,
            created_by=created_by,
            promised_date=promised_date,
            scheduled_start_date=scheduled_start_date,
            scheduled_completion_date=scheduled_completion_date,
        )
        order_line = OrderLine(
            order=order,
            item_id=finished_good.id,
            quantity=quantity,
            promised_date=promised_date,
            scheduled_start_date=scheduled_start_date,
            scheduled_completion_date=scheduled_completion_date,
        )
        db.session.add(order)
        db.session.add(order_line)

        bom_entities = {}
        for component_entry in bom_components:
            component_item = component_entry["item"]
            component_quantity = component_entry["quantity"]
            bom_component = OrderComponent(
                order_line=order_line,
                component_item_id=component_item.id,
                quantity=component_quantity,
            )
            db.session.add(bom_component)
            bom_entities[component_entry["sku"]] = bom_component
            db.session.add(
                Reservation(
                    order_line=order_line,
                    item_id=component_item.id,
                    quantity=component_quantity * quantity,
                )
            )

        for step in sorted(routing_steps, key=lambda step: step["sequence"]):
            routing_step = RoutingStep(
                order=order,
                sequence=step["sequence"],
                work_cell=step["work_cell"] or None,
                description=step["instructions"],
            )
            db.session.add(routing_step)
            for component_sku in step["components"]:
                db.session.add(
                    RoutingStepComponent(
                        routing_step=routing_step,
                        order_component=bom_entities[component_sku],
                    )
                )

        db.session.commit()
        flash("Order created", "success")
        return redirect(url_for("orders.view_order", order_id=order.id))

    return render_template("orders/new.html", items=items, form_data=form_data)


@bp.route("/<int:order_id>")
def view_order(order_id):
    order = (
        Order.query.options(
            joinedload(Order.order_lines)
            .joinedload(OrderLine.components)
            .joinedload(OrderComponent.component_item),
            joinedload(Order.order_lines).joinedload(OrderLine.item),
            joinedload(Order.order_lines)
            .joinedload(OrderLine.reservations)
            .joinedload(Reservation.item),
            joinedload(Order.routing_steps)
            .joinedload(RoutingStep.component_links)
            .joinedload(RoutingStepComponent.order_component)
            .joinedload(OrderComponent.component_item),
        )
        .filter_by(id=order_id)
        .first_or_404()
    )
    return render_template("orders/view.html", order=order)


@bp.route("/<int:order_id>/routing", methods=["POST"])
def update_routing(order_id):
    order = (
        Order.query.options(joinedload(Order.routing_steps))
        .filter_by(id=order_id)
        .first_or_404()
    )
    selected_ids = set()
    for raw_id in request.form.getlist("completed_steps"):
        try:
            selected_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue

    changes_made = False
    current_time = datetime.utcnow()
    for step in order.routing_steps:
        desired_state = step.id in selected_ids
        if step.completed != desired_state:
            step.completed = desired_state
            step.completed_at = current_time if desired_state else None
            changes_made = True

    if changes_made:
        db.session.commit()
        flash("Routing progress updated", "success")
    else:
        flash("No routing updates were made.", "info")

    return redirect(url_for("orders.view_order", order_id=order.id))


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
