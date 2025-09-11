from flask import Blueprint, render_template, request
from datetime import datetime
from invapp.models import Order

bp = Blueprint("orders", __name__, url_prefix="/orders")

@bp.route("/")
def orders_home():
    return render_template("orders/home.html")


@bp.route("/open")
def open_orders():
    return _list_orders("open", "Open Orders")


@bp.route("/closed")
def closed_orders():
    return _list_orders("closed", "Closed Orders")


def _list_orders(default_status, title):
    due_date_str = request.args.get("due_date")
    sku = request.args.get("sku")
    status = request.args.get("status", default_status)

    query = Order.query
    if status:
        query = query.filter(Order.status == status)
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            query = query.filter(Order.due_date == due_date)
        except ValueError:
            pass
    if sku:
        query = query.filter(Order.sku == sku)

    orders = query.all()
    filters = {"due_date": due_date_str, "sku": sku, "status": status}
    return render_template("orders/list.html", orders=orders, title=title, filters=filters)
