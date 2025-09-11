from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from invapp.models import db, Item, BOMLine, RoutingStep
from sqlalchemy.orm import joinedload

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


@bp.route("/bom")
def list_bom():
    lines = BOMLine.query.options(
        joinedload(BOMLine.parent_item), joinedload(BOMLine.component_item)
    ).all()
    return render_template("orders/bom_list.html", bom_lines=lines)


@bp.route("/bom/add", methods=["GET", "POST"])
def add_bom():
    if request.method == "POST":
        fg_sku = request.form["fg_sku"].strip()
        component_sku = request.form["component_sku"].strip()
        quantity = float(request.form["quantity"])

        fg_item = Item.query.filter_by(sku=fg_sku).first()
        comp_item = Item.query.filter_by(sku=component_sku).first()
        if not fg_item or not comp_item:
            flash("Invalid SKU provided.", "danger")
            return redirect(url_for("orders.add_bom"))

        line = BOMLine(
            parent_item_id=fg_item.id,
            component_item_id=comp_item.id,
            quantity=quantity,
        )
        db.session.add(line)
        db.session.commit()
        flash("BOM line added.", "success")
        return redirect(url_for("orders.list_bom"))

    return render_template("orders/bom_form.html")


@bp.route("/bom/edit/<int:bom_id>", methods=["GET", "POST"])
def edit_bom(bom_id):
    line = BOMLine.query.get_or_404(bom_id)

    if request.method == "POST":
        fg_sku = request.form["fg_sku"].strip()
        component_sku = request.form["component_sku"].strip()
        quantity = float(request.form["quantity"])

        fg_item = Item.query.filter_by(sku=fg_sku).first()
        comp_item = Item.query.filter_by(sku=component_sku).first()
        if not fg_item or not comp_item:
            flash("Invalid SKU provided.", "danger")
            return redirect(url_for("orders.edit_bom", bom_id=bom_id))

        line.parent_item_id = fg_item.id
        line.component_item_id = comp_item.id
        line.quantity = quantity
        db.session.commit()
        flash("BOM line updated.", "success")
        return redirect(url_for("orders.list_bom"))

    return render_template(
        "orders/bom_form.html",
        line=line,
        fg_sku=line.parent_item.sku,
        component_sku=line.component_item.sku,
    )


@bp.route("/bom/delete/<int:bom_id>", methods=["POST"])
def delete_bom(bom_id):
    line = BOMLine.query.get_or_404(bom_id)
    db.session.delete(line)
    db.session.commit()
    flash("BOM line deleted.", "success")
    return redirect(url_for("orders.list_bom"))


@bp.route("/routing")
def list_routing():
    steps = RoutingStep.query.options(joinedload(RoutingStep.item)).order_by(
        RoutingStep.item_id, RoutingStep.step_number
    ).all()
    return render_template("orders/routing_list.html", steps=steps)


@bp.route("/routing/add", methods=["GET", "POST"])
def add_routing():
    if request.method == "POST":
        item_sku = request.form["item_sku"].strip()
        step_number = int(request.form["step_number"])
        description = request.form["description"].strip()

        item = Item.query.filter_by(sku=item_sku).first()
        if not item:
            flash("Invalid SKU.", "danger")
            return redirect(url_for("orders.add_routing"))

        step = RoutingStep(
            item_id=item.id, step_number=step_number, description=description
        )
        db.session.add(step)
        db.session.commit()
        flash("Routing step added.", "success")
        return redirect(url_for("orders.list_routing"))

    return render_template("orders/routing_form.html")


@bp.route("/routing/edit/<int:step_id>", methods=["GET", "POST"])
def edit_routing(step_id):
    step = RoutingStep.query.get_or_404(step_id)

    if request.method == "POST":
        item_sku = request.form["item_sku"].strip()
        step_number = int(request.form["step_number"])
        description = request.form["description"].strip()

        item = Item.query.filter_by(sku=item_sku).first()
        if not item:
            flash("Invalid SKU.", "danger")
            return redirect(url_for("orders.edit_routing", step_id=step_id))

        step.item_id = item.id
        step.step_number = step_number
        step.description = description
        db.session.commit()
        flash("Routing step updated.", "success")
        return redirect(url_for("orders.list_routing"))

    return render_template(
        "orders/routing_form.html", step=step, item_sku=step.item.sku
    )


@bp.route("/routing/delete/<int:step_id>", methods=["POST"])
def delete_routing(step_id):
    step = RoutingStep.query.get_or_404(step_id)
    db.session.delete(step)
    db.session.commit()
    flash("Routing step deleted.", "success")
    return redirect(url_for("orders.list_routing"))
